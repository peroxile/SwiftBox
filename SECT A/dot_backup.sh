#!/usr/bin/env bash
set -euo pipefail
# set -x 

IFS=$'\n\t'


# Default (can pass a different source dir as first arg)
DEFAULT_HOME="${1:-$HOME}"
BACKUP_DIR="/var/backups"
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
TMPDIR="$(mktemp -d /tmp/dotbackup_XXXX)"
ARCHIVE_NAME="dotfiles_backup_${TIMESTAMP}.tar"
ARCHIVE_PATH="${TMPDIR}/${ARCHIVE_NAME}"
COMPRESSED_PATH="${ARCHIVE_PATH}.gz"
FILES_LIST="${TMPDIR}/files.lst"

cleanup() {
    rm -rf -- "$TMPDIR"
}

trap cleanup EXIT

ensure_backup_dir() {
    if [[ ! -d "$BACKUP_DIR" ]]; then
        if mkdir -p "$BACKUP_DIR" 2>/dev/null; then
        :
        elif command -v sudo >/dev/null 2>&1; then 
            sudo mkdir -p "$BACKUP_DIR"
        else
            echo "ERROR: Cannot create $BACKUP_DIR and sudo not available." >&2
        exit 1
        fi 
    fi

    if [[ -w "$BACKUP_DIR" ]]; then
        if command -v sudo >/dev/null 2>&1; then
        : 
        else 
            echo "ERROR: No write permission to $BACKUP_DIR and sudo not available." >&2
        exit 1
        fi 
    fi 
        
}


compress_files() {
    if [[ ! -s "$FILES_LIST" ]]; then
        echo "No dotfiles found under $DEFAULT_HOME. Nothing to backup."
        return 1
    fi 
    # Create tar with null-delimited input to preserve paths containing whitespace/newlines
    tar --null -cf "$ARCHIVE_PATH" -T "$FILES_LIST"
    gzip -9 -- "$ARCHIVE_PATH"
    echo "Created compressed archive: $COMPRESSED_PATH"
    return 0
}

 
encrypt_if_requested() {
    local src="$1"
    local final="$src"
    read -r -p "Encrypt archive before copying to ${BACKUP_DIR}?[y/N]:" ans
    if [[ "$ans" = ~^[Yy]$ ]]; then
        if command -v gpg >/dev/null 2>&1; then
            echo "Using gpg symmetric encryption (AES256)."
            # read passphrase silently
            read -r -s -p "Passphrase:" pass
        echo 
        # Use --batch --yes and passphrase via fd for security 
        printf "%s" "$pass" | gpg --symmetric --cipher-algo AES256 --batch --yes --passphrase-fd 0 -o "${src}.gpg" "$src" final="${src}.gpg"
        else
        echo "gpg not found, failing back to openssl."
        read -r -s -p "Passphrase:" pass
        echo 
        # Use PBKDF2 and many iteration
        openssl enc -aes-256-cbc -pbkdf2 -iter 1000000 -salt -pass:"$pass" -in "$src" -out "${src}.enc"
        final="${src}.enc"
        fi 
    fi 
    echo "$final"

}

safe_copy_to_backup() {
    local file="$1"
    ensure_backup_dir
    local dest
    dest="${BACKUP_DIR}/$(basename "$file")"
    if [[ -w "$BACKUP_DIR" ]]; then
        cp -v -- "$file" "$dest"
    else 
        sudo cp -v -- "$file" "$dest"
    fi
    echo "Backup stored at: $dest"
}

# Main 
copy_file(){
    collect_dotfiles || return 1
    compress_files || return 1
    local final
    final="$(encrypt_if_requested "$COMPRESSED_PATH")"
    safe_copy_to_backup "$final"
}

# Simple restore helper: lists backups in /var/backups and optionally restore one.

locate_setup() {
    ls -1t "${BACKUP_DIR}" 2>/dev/null || { echo "No backups in ${BACKUP_DIR}."; return 1; } 
    read -r -p "Enter backup filename to restore (or empty to abort):" choice
    if [[ -z "$choice" ]]; then
        echo "Abort"
    return 1
    fi

    local src="${BACKUP_DIR%/}/$choice"
    if [[ ! -f "$src" ]]; then 
        echo "Not found: $src" >&2
        return 1
    fi 

    # If file looks like a gpg or .enc, attempt to decrypt to tmp
    local extracted="${TMPDIR}/restore_archive.tar"
    case "$src" in 
        *.gpg)
            read -r -s -p "Passphrase to decrypt ${choice}:" pass
            echo 
            printf "%s" "$pass" | gpg --batch --yes --passphrase-fd 0 -o "${extracted}.gz" -d "$src"
            gzip -d --force "${extracted}.gz"
            tar -xf "$extracted" -C "$HOME"
            echo "Restored archive $choice to $HOME"
        ;;
        *.enc)
            read -r -s -p "Passphrase to decrypt ${choice}:" pass
            echo 
            openssl enc -d -aes-256-cbc -pbkdf2 -iter 100000 -pass pass: -in "$src" -out "${extracted}.gz"
            gzip -d --force "${extracted}.gz"
            tar -xf "$extracted" -C "$HOME"
            echo "Restored archive $choice to $HOME"
        ;;
        *.tar)
            cp -v -- "$src" "${TMPDIR}/"
            tar -xf "${TMPDIR}/$(basename "$src")" -C "$HOME"
            echo "Restored archive $choice to $HOME"
        ;;
        *)
            echo "Unknown file type. Manual inspect required." >&2
            return 1
        ;;
    esac
} 

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    copy_file
fi