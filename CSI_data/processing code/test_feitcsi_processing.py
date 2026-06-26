#!/usr/bin/env python3

import csv
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import numpy as np

import PicoSense_csi_matcher
import PicoSense_csi_merger


class FeitCsiProcessingTest(unittest.TestCase):
    def test_match_and_merge_four_cards_with_one_missing_frame(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            db_root = root / "db"
            artifacts_root = root / "artifacts"
            intermediates_root = root / "intermediates"
            exp_name = "20260613_test"
            topics = ["1", "2", "3", "4"]
            nic_ids = ["51", "52", "53", "54"]
            pcis = ["0000:07:00.0", "0000:08:00.0", "0000:09:00.0", "0000:0a:00.0"]
            base_ns = 1_000_000_000

            for topic_index, (topic, nic_id, pci) in enumerate(zip(topics, nic_ids, pcis)):
                csv_dir = db_root / exp_name / f"csi.rx.{topic}"
                array_dir = artifacts_root / exp_name / "arrays" / f"csi.rx.{topic}"
                csv_dir.mkdir(parents=True)
                array_dir.mkdir(parents=True)
                rows = []
                for frame_index in range(5):
                    if topic == "2" and frame_index == 2:
                        continue
                    filename = f"topic{topic}_seq{frame_index + 1}.npy"
                    value = topic_index * 10 + frame_index
                    array = np.full(
                        (1992, 2, 2, 1),
                        value + 1j * (value + 1),
                        dtype=np.complex64,
                    )
                    np.save(array_dir / filename, array)
                    rows.append({
                        "nic_id": nic_id,
                        "pci": pci,
                        "rx_seq": frame_index + 1,
                        "rx_system_ns": (
                            base_ns
                            + frame_index * 1_000_000
                            + topic_index * 50_000
                        ),
                        "array_saved": str(array_dir / filename),
                        "error": "",
                    })

                with (csv_dir / "20260613_1200.csv").open(
                    "w", newline="", encoding="utf-8"
                ) as handle:
                    writer = csv.DictWriter(
                        handle,
                        fieldnames=[
                            "nic_id",
                            "pci",
                            "rx_seq",
                            "rx_system_ns",
                            "array_saved",
                            "error",
                        ],
                    )
                    writer.writeheader()
                    writer.writerows(rows)

            matcher_args = SimpleNamespace(
                db_root=str(db_root),
                intermediates_root=str(intermediates_root),
                topics=topics,
                nic_ids=nic_ids,
                reference_topic="1",
                tolerance_us=300.0,
            )
            matched_path = Path(PicoSense_csi_matcher.match_experiment(exp_name, matcher_args))
            with matched_path.open(newline="", encoding="utf-8") as handle:
                matched_rows = list(csv.DictReader(handle))

            self.assertEqual(len(matched_rows), 5)
            self.assertEqual(matched_rows[2]["topic2_array_saved"], "")
            self.assertEqual(matched_rows[0]["topic3_pci"], "0000:09:00.0")
            self.assertEqual(int(matched_rows[0]["topic4_delta_ns"]), 150_000)

            merger_args = SimpleNamespace(
                artifacts_root=str(artifacts_root),
                intermediates_root=str(intermediates_root),
                data_root=str(root / "dataset"),
                topics=topics,
                nic_ids=nic_ids,
                subcarriers=512,
                antenna_order=[str(index) for index in range(8)],
                dataset_type="rt",
                missing_policy="interpolate",
                keep_csd=False,
            )
            output = PicoSense_csi_merger.merge_experiment(exp_name, merger_args)
            with np.load(output) as merged:
                self.assertEqual(merged["csi"].shape, (5, 2, 8, 512))
                self.assertEqual(merged["valid_mask"].shape, (5, 8))
                self.assertEqual(int(merged["topic_valid_mask"].sum()), 19)
                self.assertFalse(np.isnan(merged["csi"]).any())
                self.assertTrue(
                    np.all(merged["csi"][2, 0, 2:4, :] == 12 + 13j)
                )
                self.assertTrue(
                    np.all(merged["csi"][0, 0, 6:8, :] == 30 + 31j)
                )
                self.assertEqual(merged["frequency_offsets_hz"].shape, (512,))
                np.testing.assert_array_equal(merged["pcis"], np.asarray(pcis))
                self.assertAlmostEqual(
                    float(merged["frequency_offsets_hz"][0]),
                    -80_000_000,
                )
                self.assertAlmostEqual(
                    float(merged["frequency_offsets_hz"][-1]),
                    80_000_000,
                )
                self.assertAlmostEqual(
                    float(merged["frequency_span_hz"]),
                    160_000_000,
                )
                self.assertAlmostEqual(
                    float(merged["frequency_spacing_hz"]),
                    160_000_000 / 511,
                )
                self.assertAlmostEqual(
                    float(merged["nominal_bandwidth_hz"]),
                    160_000_000,
                )
                self.assertTrue(bool(merged["frequency_edge_extrapolated"]))
                self.assertTrue(bool(merged["frequency_resampled"]))
                self.assertTrue(bool(merged["csd_removed"]))
                np.testing.assert_array_equal(
                    merged["packet_loss"],
                    np.asarray([0, 1, 0, 0]),
                )
                np.testing.assert_array_equal(
                    merged["total_packet"],
                    np.asarray([5, 5, 5, 5]),
                )
                self.assertEqual(int(merged["overall_packet_loss"]), 1)
                self.assertEqual(int(merged["overall_total_packet"]), 20)
                self.assertAlmostEqual(float(merged["overall_loss_rate"]), 0.05)

            summary_path = output.parent / f"{exp_name}_packet_loss.csv"
            self.assertFalse(summary_path.exists())

    def test_csd_removal_matches_picoscenes_rotation_without_resampling(self):
        indices = PicoSense_csi_merger.he160_subcarrier_indices(1992)
        array = np.ones((2, 1, 1992), dtype=np.complex64)
        corrected = PicoSense_csi_merger.remove_csd(array, indices)

        self.assertEqual(corrected.shape, array.shape)
        np.testing.assert_allclose(corrected[0], 1 + 0j)
        expected_sts2 = np.exp(
            2j * np.pi * indices * (-64) / 2048
        ).astype(np.complex64)
        np.testing.assert_allclose(corrected[1, 0], expected_sts2, rtol=1e-6)

    def test_resamples_full_160mhz_to_512_equal_frequency_points(self):
        indices = PicoSense_csi_merger.he160_subcarrier_indices(1992)
        values = indices.astype(np.float32) + 1j * (2 * indices.astype(np.float32))
        array = values.reshape(1, 1, 1992).astype(np.complex64)
        resampled, target_indices = PicoSense_csi_merger.resample_full_bandwidth(
            array,
            indices,
            512,
        )

        self.assertEqual(resampled.shape, (1, 1, 512))
        self.assertEqual(target_indices[0], -1024)
        self.assertEqual(target_indices[-1], 1024)
        np.testing.assert_allclose(np.diff(target_indices), 2048 / 511)
        np.testing.assert_allclose(
            resampled[0, 0],
            target_indices + 1j * (2 * target_indices),
            rtol=1e-5,
            atol=1e-4,
        )


if __name__ == "__main__":
    unittest.main()
