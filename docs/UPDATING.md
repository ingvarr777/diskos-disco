# Updating from the SD card

[Back to the README](../README.md) · [Developer workflow](DEV_WORKFLOW.md)

Flash once with your own public update key. After that, new **interface builds** install from the SD card: no
reflash, no SSH. The Disc checks the signatures, runs the new build as a trial, and goes back to the previous
build if the trial fails.

An SD update changes `mq_ui` (the interface) only. It doesn't touch the stock player, the boot scripts or the
optional apps ([Album Roulette](ALBUM_ROULETTE_INSTALL.md), [Disc Shelf](DISC_SHELF_INSTALL.md)). A downloaded
`mq_ui` is **not** signed with your keys yet; you sign it yourself (step 3).

## 1. Make keys and flash once

**Already flashed with an update key?** Keep using that same keys folder and skip to step 2. A new key is not
trusted by an existing install.

First install, from the repository root on your Linux computer:

```sh
sh ui/tools/owner-keys/diskos-keys.sh
cp diskos-keys/diskos-root.pub.pem payload/
sha256sum diskos-keys/diskos-root.pub.pem
./diskos-installer install \
  --firmware SNOWSKY_DISC_update_20260909_v257.zip \
  --variant public \
  --ui payload/mq_ui \
  --ota-key payload/diskos-root.pub.pem
```

Power the Disc off, hold Volume Down and connect USB when the installer asks. Before you confirm, check that
`ota: ON` shows the start of the hash you just printed. If it says `OFF` or another hash, answer **N** and fix the
command. Use the public variant (dev's USB serial gadget blocks USB storage).

| Key file | Keep it |
|---|---|
| `root.key` | Private, backed up offline. Only needed to authorize leaf keys. |
| `leaf.key` | Private, on your computer, backed up. Signs every update. |
| `diskos-root.pub.pem` / `root.pub.pem` | Public: the key installed on the Disc. |
| `leafpub.pem`, `keyauthz`, `keyauthz.sig` | Public: copied into every package. |

Back up the whole keys folder and keep private keys out of the repository, downloads and the SD card. Lost keys:
restore the backup (a different root key means reflashing). Don't make new keys to fix an ordinary rejected update.

## 2. Get and check the interface

Download `mq_ui` and `SHA256SUMS` from the [latest release](https://github.com/zmd22/diskos-disco/releases/latest)
into one folder, then:

```sh
awk '$2 == "mq_ui" { print }' SHA256SUMS | sha256sum -c -
```

Expect `mq_ui: OK`. If you build it yourself ([DEV_WORKFLOW.md](DEV_WORKFLOW.md)), the result is `ui/mq_ui`. Never
use the desktop `host-render` program on the Disc.

## 3. Sign it

From the repository root, with your keys in `./diskos-keys`:

```sh
sh ui/tools/owner-keys/diskos-sign.sh path/to/mq_ui 1.4.0
```

With keys elsewhere: `diskos-sign.sh <binary> [label] [keys-folder] [output-folder]`. The label (up to 32 letters,
digits, `.`, `-`, `_`) only names the package; About shows the version built into `mq_ui`. The script empties and
recreates its output folder, so never point it at your keys or source folder.

**Epochs.** Each package gets an epoch: whole minutes since 2026-01-01 (UTC), from your computer's clock. The Disc
only accepts a higher epoch than the last one it accepted; the version label doesn't matter. So keep your clock
right, and to retest, simply sign again a minute later. An old folder is refused as a downgrade.

**A rolled-back build can't be retried.** When a trial rolls back, the Disc remembers that `mq_ui` (by its hash,
the last eight builds) and refuses it from then on, however it is signed. Signing it again doesn't help: rebuild it
(any change gives a new hash) and sign the new `mq_ui`.

Optional check on the computer:

```sh
sh payload/diskos-verify.sh /path/to/diskos-keys/diskos-root.pub.pem 1 1 ./diskos-update ./diskos-update/mq_ui
```

Expect `OK component=mq_ui ...`. This proves the package is intact, not that the key matches your Disc.

## 4. Copy the folder to the card

Copy the whole `diskos-update` folder to the top of the SD card, so the path is `<SD>/diskos-update/mq_ui` (not
`diskos-update/diskos-update/...`). It holds six files from one signing run: `mq_ui`, `update.manifest` and
`update.manifest.sig`, `leafpub.pem`, `keyauthz` and `keyauthz.sig`. Don't mix files from different runs.

Eject the card safely. If you used the Disc's USB storage mode, eject on the computer, then switch back to Playback.

## 5. Install and keep the trial

1. **Settings > System > Maintenance > Update from SD Card > Update.** Wait for staging, then restart when asked.
2. Check **Settings > System > About** for the expected version and build ID.
3. Use it for at least **three minutes** and play some music. After that, music that actually plays accepts the
   build by itself; otherwise answer **Keep** when asked (or **Go back** to revert). Just browsing doesn't count.
4. Restart once more and check it still runs: the build is now kept.

A trial that never proves itself gets two boots, then rolls back; one that keeps crashing rolls back sooner. A
rollback returns to the previous kept build (or the flashed one); stock is the last fallback. Avoid restarting
repeatedly before the trial is accepted.

## Troubleshooting

| Symptom | What to check |
|---|---|
| No update key installed | Reflash once with `--ota-key`; signing alone can't add trust. |
| Update folder not found | `diskos-update` at the card's top level, all six files present. |
| Update refused / damaged | Verify on the computer, compare the root key hash (below), recopy the whole folder, check the epoch. |
| Same or older epoch | Sign again a minute later with a correct clock and replace the whole folder. |
| Not enough space | The Disc's own data partition is full; free space on the SD card doesn't help. |
| Old build after a trial | The trial was rolled back or never accepted; check About. |
| "This build was rolled back before" | That exact `mq_ui` failed a trial on this Disc. Rebuild it, then sign and copy again. |
| Old build after flashing | Check which file you passed with `--ui`. A new flash clears old SD builds. |

For details, turn on **Settings > System > Maintenance > Debug Mode** and use the address it shows:

```sh
DISKOS_IP=192.168.1.50  # the address shown in Debug Mode
ssh "root@$DISKOS_IP" 'cat /usr/data/updates/last_result'
ssh "root@$DISKOS_IP" 'cat /tmp/.diskos_run; cat /usr/data/updates/accepted'
ssh "root@$DISKOS_IP" 'tail -n 30 /usr/data/diskos_install.log'
ssh "root@$DISKOS_IP" 'sha256sum /etc/diskos-ota/root.pub.pem'
```

The root hash must match `sha256sum diskos-keys/diskos-root.pub.pem` on your computer. Some files only exist after
a first update. Note the message shown on the Disc too. Never copy `mq_ui` over SSH or edit the boot manifest:
the Disc would reject it and fall back to stock.

## Returning to stock

**Settings > System > Controls > Default UI > Stock** makes FiiO's interface the default; holding Volume Up from
power-on boots the other one once. Boot scripts and root keys can only be changed by flashing with the installer.
See the [README](../README.md#going-back).
