#!/usr/bin/env bash
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
ssh -i "$HOME/.ssh/fairplay_dkuoj_ed25519" -o IdentitiesOnly=yes -o BatchMode=yes -o StrictHostKeyChecking=yes ax19@10.200.31.64 'mkdir -p "$HOME/fairplay-reviewed-prs"'
scp -i "$HOME/.ssh/fairplay_dkuoj_ed25519" -o IdentitiesOnly=yes "$HERE/0030-FairPlay-discovery-and-audience-editor.patch" "$HERE/baseline.json" "$HERE/after.json" "$HERE/verification.json" "$HERE/apply-professor.sh" ax19@10.200.31.64:~/fairplay-reviewed-prs/
ssh -t -i "$HOME/.ssh/fairplay_dkuoj_ed25519" -o IdentitiesOnly=yes ax19@10.200.31.64 'cd "$HOME/fairplay-reviewed-prs" && sudo bash apply-professor.sh --check && sudo bash apply-professor.sh --apply'
