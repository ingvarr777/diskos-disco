"""S97diskos_install's SD-update selection (ota_select), run for real on the host.

The shipped script runs under the host /bin/sh with its device paths (/usr/data, /etc/diskos-ota, /etc/diskos_manifest,
/opt/diskos, /tmp/.diskos*) moved into a temp root, and bundles are signed with real keys from
ui/tools/owner-keys/diskos-keys.sh and checked by the real payload/diskos-verify.sh. The UI binaries are tiny
fake ELF32-LE MIPS files of a real build's size: only their header, size and sha256 are ever checked here.

OtaBootFirmwareAshTest repeats every scenario under the stock firmware's own BusyBox (via qemu-mipsel-static, as
test_boot_select.py does), with ONLY its applets on PATH, so the firmware's missing `timeout` is missing here too.
openssl stays the host's (the verifier calls it by absolute path) and qemu -L is a loader prefix, not a filesystem
boundary. Not established here: real NAND/ubifs behaviour when /usr/data is full, and timing on the device.
"""
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
S97 = REPO / 'payload' / 'S97diskos_install'
VERIFIER = REPO / 'payload' / 'diskos-verify.sh'
KEYGEN = REPO / 'ui' / 'tools' / 'owner-keys' / 'diskos-keys.sh'
HAVE_TOOLS = all(shutil.which(t) for t in ('openssl', 'setsid', 'timeout', 'sha256sum'))
# the stock firmware whose BusyBox runs S97 on the Disc (same variables and default location as test_boot_select.py)
FW_BASE = os.environ.get('DISKOS_FW_BASE', '257')
FW_ROOT = Path(os.environ.get('DISKOS_FW_ROOT') or REPO.parent / 'scratch' / 'fixtures' / ('v' + FW_BASE))
FW_BUSYBOX_MD5 = {'209': 'e6b2bb328e1c08238878991c0aae062d', '228': 'c1af60e81cea22b040b957c00404de9e',
                  '240': '5d8a5e60ee7d40002fe70f43228596cb', '257': '8651f07f6387df9f10a22a73f0f9450f'}
QEMU = shutil.which('qemu-mipsel-static')


def _have_fw_ash():
    import hashlib
    bb = FW_ROOT / 'bin' / 'busybox'
    return (bool(QEMU) and bb.is_file()
            and hashlib.md5(bb.read_bytes()).hexdigest() == FW_BUSYBOX_MD5.get(FW_BASE))


def fake_ui(tag):
    """A file that passes S97's ELF32-LE MIPS header checks; `tag` makes each build's hash distinct."""
    hdr = bytearray(52)
    hdr[0:4] = b'\x7fELF'
    hdr[4:6] = b'\x01\x01'
    hdr[18:20] = b'\x08\x00'
    body = tag.encode() * 64
    return bytes(hdr) + body + bytes(3_400_000 - len(hdr) - len(body))   # about the size of a real mq_ui


class OtaBootTest(unittest.TestCase):
    keys = None

    @classmethod
    def setUpClass(cls):
        if not HAVE_TOOLS:
            raise unittest.SkipTest('needs openssl, setsid, timeout and sha256sum')
        cls._kd = tempfile.TemporaryDirectory(prefix='diskos-ota-keys-')
        cls.keys = Path(cls._kd.name) / 'keys'
        subprocess.run(['sh', str(KEYGEN), str(cls.keys)], check=True, capture_output=True)

    @classmethod
    def tearDownClass(cls):
        cls._kd.cleanup()

    def setUp(self):
        self._td = tempfile.TemporaryDirectory(prefix='diskos-ota-boot-')
        self.root = Path(self._td.name)
        r = self.root
        for d in ('usr/data', 'etc/diskos-ota', 'opt/diskos', 'tmp', 'bin'):
            (r / d).mkdir(parents=True)
        self.data = r / 'usr/data'
        self.upd = self.data / 'updates'
        self.log = self.data / 'diskos_install.log'
        self.flashed = fake_ui('flashed')
        (r / 'opt/diskos/mq_ui').write_bytes(self.flashed)
        (r / 'etc/diskos_manifest').write_text('SHA256=%s\nSIZE=%d\n' % (self.sha(self.flashed), len(self.flashed)))
        shutil.copy(self.keys / 'diskos-root.pub.pem', r / 'etc/diskos-ota/root.pub.pem')
        (r / 'etc/diskos-ota/epochs').write_text('MIN_KEY_EPOCH=1\nMIN_UI_EPOCH=1\n')
        shutil.copy(VERIFIER, r / 'etc/diskos-ota/diskos-verify.sh')
        selected = r / 'bin/diskos-selected'
        selected.write_text('#!/bin/sh\nexit 0\n')
        selected.chmod(0o755)
        text = S97.read_text()
        for old in ('/usr/data', '/etc/diskos-ota', '/etc/diskos_manifest', '/opt/diskos/mq_ui', '/tmp/.diskos'):
            text = text.replace(old, str(r) + old)
        self.path = os.environ.get('PATH', '/usr/bin:/bin')
        self.shell = '/bin/sh'
        text = self.adapt(text)
        self.script = r / 'S97test'
        self.script.write_text(text)
        self.script.chmod(0o755)

    def adapt(self, text):
        return text

    def tearDown(self):
        self._td.cleanup()

    @staticmethod
    def sha(b):
        import hashlib
        return hashlib.sha256(b).hexdigest()

    def boot(self):
        """One boot's S97 (supervisor + worker), synchronously, as rcS runs it."""
        env = {'PATH': self.path, 'DISKOS_SELECTED': str(self.root / 'bin/diskos-selected')}
        subprocess.run([self.shell, str(self.script), 'start'], env=env, check=True, timeout=600)
        return self.log.read_text() if self.log.exists() else ''

    def bundle(self, dest, ui, epoch, version):
        """A signed bundle exactly as diskos-sign.sh makes it, with a chosen epoch, plus the UI's `ready` file."""
        dest.mkdir(parents=True)
        (dest / 'mq_ui').write_bytes(ui)
        for f in ('leafpub.pem', 'keyauthz', 'keyauthz.sig'):
            shutil.copy(self.keys / f, dest / f)
        (dest / 'update.manifest').write_text(
            'diskos-update-v1\ncomponent=mq_ui\nepoch=%d\nversion=%s\nsize=%d\nsha256=%s\nchannel=sd\n'
            % (epoch, version, len(ui), self.sha(ui)))
        subprocess.run(['openssl', 'dgst', '-sha256', '-sign', str(self.keys / 'leaf.key'), '-out',
                        str(dest / 'update.manifest.sig'), str(dest / 'update.manifest')], check=True)
        (dest / 'ready').write_text('version=%s\nepoch=%d\n' % (version, epoch))

    def stage(self, ui, epoch, version):
        self.upd.mkdir(mode=0o700, exist_ok=True)
        self.bundle(self.upd / 'pending', ui, epoch, version)

    def installed(self):
        return (self.data / 'mq_ui').read_bytes()

    def run_record(self):
        p = self.root / 'tmp/.diskos_run'
        return p.read_text() if p.exists() else ''

    def mark_healthy(self, ui):
        """What the running trial's UI writes (ota_health_write) plus the launcher's trial.launched record."""
        for name in ('healthy', 'trial.launched'):
            (self.upd / name).write_text('SHA256=%s\n' % self.sha(ui))

    # ------------------------------------------------------------------ first update on a fresh flash (#7)
    def test_first_sd_update_after_a_fresh_flash_is_kept(self):
        self.boot()                                            # fresh flash: no staging directory yet
        self.assertEqual(self.installed(), self.flashed)
        self.assertFalse(self.upd.exists())
        new = fake_ui('one')
        self.stage(new, 100, '1.4.1')                          # the UI creates updates/ to stage the first update
        log = self.boot()
        self.assertNotIn('kept SD-card builds dropped', log)
        self.assertIn('staged 1.4.1 (epoch 100) verified -> trial boot 1', log)
        self.assertEqual(self.installed(), new)
        self.assertIn('BUILD=trial', self.run_record())
        self.assertEqual((self.upd / 'flashed').read_text().strip(), 'SHA256=' + self.sha(self.flashed))

    def test_builds_kept_from_an_earlier_flash_are_still_dropped(self):
        """Control: with no `flashed` record, a build that already ran (good/) means /usr/data predates this flash."""
        self.upd.mkdir(mode=0o700)
        self.bundle(self.upd / 'good', fake_ui('old'), 50, '1.0.0')
        log = self.boot()
        self.assertIn('kept SD-card builds dropped', log)
        self.assertFalse((self.upd / 'good').exists())
        self.assertEqual(self.installed(), self.flashed)

    # ------------------------------------------------------------------ the #8 shape, end to end on the boot side
    def test_second_update_run_record_carries_the_good_build(self):
        a, b = fake_ui('a'), fake_ui('b')
        self.boot()
        self.stage(a, 100, '1.3.0-Disco')
        self.boot()
        self.mark_healthy(a)
        self.assertIn('PROMOTED 1.3.0-Disco', self.boot())
        self.stage(b, 200, '1.3.1-Disco')
        self.assertIn('staged 1.3.1-Disco (epoch 200) verified -> trial boot 1', self.boot())
        rec = self.run_record()
        self.assertIn('BUILD=trial', rec)
        self.assertIn('GOOD_SHA256=' + self.sha(a), rec)
        self.assertGreater(len(rec.encode()), 255, 'the record the UI must read (ui/tests/ota_trial_test.c)')
        self.mark_healthy(b)
        self.assertIn('PROMOTED 1.3.1-Disco', self.boot())
        self.assertEqual(self.installed(), b)

    # ------------------------------------------------------------------ ENOSPC on the boot counter (#7)
    def test_unwritable_boot_counter_rolls_back_without_blacklisting(self):
        new = fake_ui('trial')
        self.boot()
        self.stage(new, 100, '1.4.1')
        self.boot()                                            # trial boot 1
        (self.upd / 'trial.tmp').mkdir()                       # the counter's tmp file can no longer be written
        log = self.boot()
        self.assertIn('could not record trial boot 2', log)
        self.assertIn('ROLLBACK (nospace)', log)
        self.assertFalse((self.upd / 'active').exists())
        self.assertNotIn(self.sha(new), (self.upd / 'rejected').read_text() if (self.upd / 'rejected').exists() else '')
        self.assertIn('reason=nospace', (self.upd / 'rollback').read_text())
        self.assertEqual(self.installed(), self.flashed)

    def test_writable_boot_counter_still_counts(self):
        """Control for the above: the normal path still advances to boot 2 and keeps the trial."""
        new = fake_ui('trial')
        self.boot()
        self.stage(new, 100, '1.4.1')
        self.boot()
        log = self.boot()
        self.assertIn('has not proven health yet (boot 2 of 2)', log)
        self.assertEqual(self.installed(), new)

    # ------------------------------------------------------------------ transient failures (#7)
    def _break_verifier(self):
        v = self.root / 'etc/diskos-ota/diskos-verify.sh'
        good = v.read_bytes()
        v.write_text('kill -KILL $$\n')                        # what the timeout's SIGKILL looks like to S97
        return lambda: v.write_bytes(good)

    def test_verifier_killed_keeps_the_good_build(self):
        a = fake_ui('a')
        self.boot()
        self.stage(a, 100, '1.3.0')
        self.boot()
        self.mark_healthy(a)
        self.boot()                                            # promoted
        self.boot()                                            # good re-verified, good.prev gone
        restore = self._break_verifier()
        log = self.boot()
        self.assertIn('good bundle not checked this boot; kept, running the flashed build', log)
        self.assertTrue((self.upd / 'good').is_dir())
        self.assertEqual(self.installed(), self.flashed)       # nothing unverified ran
        restore()
        self.boot()
        self.assertEqual(self.installed(), a)

    def test_tampered_good_build_is_still_dropped(self):
        """Control: a real REJECT from the verifier still removes the bundle."""
        a = fake_ui('a')
        self.boot()
        self.stage(a, 100, '1.3.0')
        self.boot()
        self.mark_healthy(a)
        self.boot()
        self.boot()
        with open(self.upd / 'good' / 'mq_ui', 'ab') as f:
            f.write(b'x')
        log = self.boot()
        self.assertIn('good bundle no longer verifies', log)
        self.assertFalse((self.upd / 'good').exists())
        self.assertEqual(self.installed(), self.flashed)

    def test_verifier_killed_keeps_the_trial_and_the_staged_build(self):
        new = fake_ui('trial')
        self.boot()
        self.stage(new, 100, '1.4.1')
        self.boot()                                            # trial boot 1
        (self.upd / 'revert').write_text('SHA256=%s\n' % self.sha(new))
        restore = self._break_verifier()
        log = self.boot()
        self.assertIn('trial bundle not checked this boot; kept', log)
        self.assertTrue((self.upd / 'active').is_dir())
        self.assertTrue((self.upd / 'revert').exists(), 'a "Go back" must survive a boot that could not act on it')
        restore()
        self.assertIn('ROLLBACK (user)', self.boot())

        self.stage(fake_ui('next'), 200, '1.4.2')
        restore = self._break_verifier()
        self.assertIn('staged bundle not checked this boot; kept', self.boot())
        self.assertTrue((self.upd / 'pending').is_dir())
        restore()
        self.assertIn('staged 1.4.2 (epoch 200) verified', self.boot())

    # ------------------------------------------------------------------ a rolled-back build staged again (#7)
    def test_restaged_rolled_back_build_is_reported(self):
        bad = fake_ui('bad')
        self.boot()
        self.stage(bad, 100, '1.4.1')
        self.boot(); self.boot()
        self.assertIn('ROLLBACK (unhealthy)', self.boot())
        (self.upd / 'rollback').unlink()
        self.assertIn(self.sha(bad), (self.upd / 'rejected').read_text())
        self.stage(bad, 200, '1.4.1-resigned')                # the same binary, signed again later
        log = self.boot()
        self.assertIn('is a build that was rolled back before', log)
        self.assertIn('reason=rejected', (self.upd / 'rollback').read_text())
        self.assertFalse((self.upd / 'pending').exists())
        self.assertEqual(self.installed(), self.flashed)

    # ------------------------------------------------------------------ housekeeping (#7)
    def test_stale_rejected_copy_does_not_block_the_flashed_ui(self):
        self.boot()
        (self.data / 'mq_ui').unlink()
        (self.data / 'mq_ui.rejected').write_bytes(fake_ui('leftover'))
        # a cp that fails while the leftover takes the space, as on a full /usr/data
        fb = self.root / 'fakebin'
        fb.mkdir()
        (fb / 'cp').write_text('#!/bin/sh\n[ -e "%s" ] && exit 1\nexec %s "$@"\n'
                               % (self.data / 'mq_ui.rejected', shutil.which('cp')))
        (fb / 'cp').chmod(0o755)
        self.path = '%s:%s' % (fb, self.path)
        log = self.boot()
        self.assertIn('removed mq_ui.rejected to make room', log)
        self.assertEqual(self.installed(), self.flashed)
        self.assertTrue((self.root / 'tmp/.diskos_ready').exists())

    def test_install_log_is_capped(self):
        self.log.write_text(''.join('old line %06d\n' % i for i in range(20000)))   # ~300 KB
        self.boot()
        text = self.log.read_text()
        self.assertLess(len(text), 131072)
        self.assertIn('old line 019999', text)                 # the newest lines are the ones kept
        self.assertNotIn('old line 000000', text)



@unittest.skipUnless(_have_fw_ash(), 'needs qemu-mipsel-static and the stock rootfs with its pinned BusyBox')
class OtaBootFirmwareAshTest(OtaBootTest):
    """Every scenario above, with S97 and the verifier run by the firmware's BusyBox ash and applets."""

    def adapt(self, text):
        fb = self.root / 'fwbin'
        fb.mkdir()
        out = subprocess.run([QEMU, '-L', str(FW_ROOT), str(FW_ROOT / 'bin/busybox'), '--list'],
                             check=True, capture_output=True, text=True).stdout.split()
        for applet in out:
            w = fb / applet
            w.write_text('#!/bin/sh\nexec %s -L %s %s %s "$@"\n' % (QEMU, FW_ROOT, FW_ROOT / 'bin/busybox', applet))
            w.chmod(0o755)
        self.assertNotIn('timeout', out, 'expected the firmware to lack `timeout` (S97 then runs unbounded steps)')
        self.path = str(fb)                                   # firmware applets only: no host fallbacks
        self.shell = str(fb / 'sh')
        return text.replace('/bin/sh', self.shell)            # the shebang and the verifier's interpreter


if __name__ == '__main__':
    unittest.main()
