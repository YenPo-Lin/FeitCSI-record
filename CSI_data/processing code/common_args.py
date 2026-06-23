import argparse

"""
File structure of db, artifacts, intermediates:
- db/
    - <exp_name>/
        - camera.frame.<camera_id>/
            - YYYYMMDD_HHMM.csv
        - csi.rx.<rx_id>/
            - YYYYMMDD_HHMM.csv
- artifacts/
    - <exp_name>/
        - arrays/
            - csi.rx.<rx_id>/
                - YYYY-MM-DDTHH-MM-SS.sssZ.npy
        - jpeg/
            - camera.frame.<camera_id>/
                - YYYY-MM-DDTHH-MM-SS.sssZ.jpg
- intermediates/
    - <exp_name>/
        - matched_csi/
            - <exp_name>_matched.csv
        - merged_csi/
            - <exp_name>_merged.npy
        - heatmaps/
            - <exp_name>_heatmaps.npz
        - matched_frames/
            - <exp_name>_frames_matched.csv
        - alphapose_raw/
            - camera.frame.<camera_id>/
                - alphapose-results.json
                - vis/
                    - YYYY-MM-DDTHH-MM-SS.sssZ.jpg
        - triangulated_poses/
            - <exp_name>_triangulated.npy
    - calibration/
        - calibration_results.json
        - <calibrate_type>/
            - camera_<camera_id>/
                - <ts>.jpg
            
"""

def build_base_parser():
    parser = argparse.ArgumentParser(description="Common arguments for RT data processing scripts.")

    parser.add_argument("--db-root", type=str, help="Path to the db directory")
    parser.add_argument("--artifacts-root", type=str, help="Path to the artifacts directory")
    parser.add_argument("--intermediates-root", type=str, help="Path to the intermediates directory")
    parser.add_argument("--calibrate-root", type=str, help="Experiment name to use for camera calibration")

    parser.add_argument("--camera-ids", nargs='+', help="List of camera IDs (numeric suffix used in folder names)", default=['1', '2', '3'])
    parser.add_argument("--root-camera-id", type=int, help="Camera ID to use as root camera for triangulation", default=2)

    parser.add_argument("--nic-ids", nargs='+', help="Physical NIC IDs in topic order", default=['51', '52', '53', '54'])
    parser.add_argument("--topics", nargs='+', help="ZMQ topic suffixes used in csi.rx.<topic>", default=['1', '2', '3', '4'])
    parser.add_argument(
        "--antenna-order",
        nargs='+',
        help=(
            "List defining the antenna order for merged CSI arrays. Default maps "
            "topics 1,3,4,2 to physical Rx1..Rx8."
        ),
        default=['0', '1', '4', '5', '6', '7', '2', '3'],
    )
    parser.add_argument(
        "--subcarriers",
        type=int,
        help="Equally spaced output points across the nominal 160 MHz bandwidth",
        default=512,
    )

    parser.add_argument("--save-drawn-corners", action='store_true', help="Whether to save images with drawn chessboard corners during calibration")

    parser.add_argument("--alphapose-root", type=str, help="Path to the AlphaPose root directory", default='AlphaPose')

    parser.add_argument("--exp-names", nargs='+', help="List of experiment names to process")

    parser.add_argument("--data-root", type=str, help="Path to the data directory")
    return parser
