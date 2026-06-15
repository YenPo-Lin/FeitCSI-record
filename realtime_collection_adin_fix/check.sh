#!/bin/bash
# Usage: ./check_incremental.sh <filename>

FILE="$1"

if [[ ! -f "$FILE" ]]; then
    echo "Error: File '$FILE' not found!"
    exit 1
fi

prev=""
line_num=0
warning=0

while IFS= read -r num; do
    ((line_num++))
    # Skip empty lines or non-numeric lines
    [[ -z "$num" || ! "$num" =~ ^[0-9]+$ ]] && continue

    if [[ -n "$prev" ]]; then
        expected=$((prev + 1))
        if (( num != expected )); then
            echo "⚠️  Gap detected at line $line_num: expected $expected but found $num"
            warning=1
        fi
    fi
    prev=$num
done < "$FILE"

if [[ $warning -eq 0 ]]; then
    echo "✅ File '$FILE' has continuous incremental numbers."
else
    echo "⚠️  File '$FILE' has one or more gaps."
fi

