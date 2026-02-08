#!/usr/bin/env bash 

set -euo pipefail
# set -x

PATH=/usr/bin:/bin
export PATH

TRASH="${XDG_DATA_HOME:-$HOME/.local/share}/Trash"

SCRIPT_PATH="$(cd -- "$(dirname -- "$0")" && pwd)/$(basename -- "$0")"

usage() {
    echo "Usage:"
    echo " $0 <time>            # run cleanup now (e.g 1h, 7d)"
    echo " $0 --cron <time>     # install cron job"
    echo " $0 --systemd <time>  # install systemd timer"
    exit 1
}

[[ $# -lt 1 ]] && usage

MODE="run"

if [[ "$1" == "--cron" || "$1" == "--systemd" ]]; then
    MODE="$1"
    shift
fi 


INPUT="${1:-}"

# Validate input 
if [[ ! "$INPUT" =~ ^([0-9]+)([mhd])$ ]]; then
    echo "Invalid format. Use: <number>[m|h|d]" >&2
    exit 1
fi

VALUE="${BASH_REMATCH[1]}"
UNIT="${BASH_REMATCH[2]}"

# Convert to minutes
case "$UNIT" in
    m) MINUTES="$VALUE" ;;
    h) MINUTES=$(( VALUE * 60 )) ;;
    d) MINUTES=$(( VALUE * 1440 )) ;;
esac


# Delete files 

cleanup() {
    exec 9>"$TRASH/.cleanup.lock"
    flock -n 9 || return 0

    [[ -d "$TRASH/files" && -d "$TRASH/info" ]] || return 0

    find "$TRASH/info" -type f -print0 |
    while IFS= read -r -d '' info; do 
        base="$(basename "$info" .trashinfo)"
        deletion_date=$(grep '^DeletionDate=' "$info" | cut -d= -f2)
        if [[ -z "$deletion_date" ]]; then continue; fi
        deletion_epoch=$(date -d "$deletion_date" +%s)
        now_epoch=$(date +%s)
        age_minutes=$(( (now_epoch - deletion_epoch)/60 ))
        if (( age_minutes > MINUTES )); then
            rm -rf --one-file-system "$TRASH/files/$base" "$info"
        fi
    done
}


install_cron() {
    crontab -l 2>/dev/null | grep -vF "$SCRIPT_PATH" |
    { cat; printf '0 * * * * "%s" "%s"\n' "$SCRIPT_PATH" "$INPUT"; } | crontab -
}

install_systemd() {
    mkdir -p ~/.config/systemd/user

    cat > ~/.config/systemd/user/empty-trash.service <<EOF
[Unit]
Description=Empty trash

[Service]
Type=oneshot
ExecStart=$SCRIPT_PATH $INPUT
EOF

    cat > ~/.config/systemd/user/empty-trash.timer <<EOF
[Unit]
Description=Empty trash timer

[Timer]
OnUnitActiveSec=1h
Persistent=true

[Install]
WantedBy=timers.target
EOF

    systemctl --user daemon-reload
    systemctl --user enable --now empty-trash.timer
}


case "$MODE" in 
    run) 
        cleanup
        ;;
    --cron)
        install_cron
        ;;
    --systemd) 
        install_systemd
        ;;
    esac