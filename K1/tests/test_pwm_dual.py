from __future__ import annotations

import json
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from devices.pwm_dual import (
    DEFAULT_CHANNELS,
    DualPwmClient,
    DualPwmController,
    DualPwmWorker,
    PwmControlError,
)


def make_fake_chip(root: Path, name: str) -> Path:
    chip = root / name
    pwm = chip / "pwm0"
    pwm.mkdir(parents=True)
    (chip / "export").write_text("")
    for node, value in (("enable", "0"), ("period", "0"), ("duty_cycle", "0"), ("polarity", "normal")):
        (pwm / node).write_text(value)
    return chip


def read_node(chip: Path, node: str) -> int:
    return int((chip / "pwm0" / node).read_text().strip())


class FakePinmux:
    def __init__(self) -> None:
        self.registers: dict[int, int] = {}
        self.writes: list[tuple[int, int]] = []

    def read(self, address: int) -> int:
        return self.registers.get(address, 0)

    def write(self, address: int, value: int) -> None:
        self.writes.append((address, value))
        self.registers[address] = value


class DualPwmControllerTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.chip4 = make_fake_chip(self.root, "pwmchip0")
        self.chip7 = make_fake_chip(self.root, "pwmchip1")
        self.pinmux = FakePinmux()
        self.state_path = self.root / "state" / "pwm_dual.json"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def make_controller(self, **kwargs) -> DualPwmController:
        return DualPwmController(
            state_path=self.state_path,
            log_path=None,
            chip_paths={"pwm4": self.chip4, "pwm7": self.chip7},
            pinmux_reader=self.pinmux.read,
            pinmux_writer=self.pinmux.write,
            **kwargs,
        )

    def assert_both(self, node: str, value: int) -> None:
        self.assertEqual(read_node(self.chip4, node), value, f"pwm4 {node}")
        self.assertEqual(read_node(self.chip7, node), value, f"pwm7 {node}")

    def test_apply_drives_both_channels_identically(self) -> None:
        controller = self.make_controller()
        status = controller.apply(enabled=True, duty=40, frequency_hz=10_000)

        self.assert_both("period", 100_000)
        self.assert_both("duty_cycle", 40_000)
        self.assert_both("enable", 1)
        self.assertTrue(status["outputs_identical"])
        self.assertTrue(all(ch["matches_target"] for ch in status["channels"]))
        self.assertEqual([ch["header_pin"] for ch in status["channels"]], [32, 40])

    def test_fresh_channel_with_zero_period_is_programmed(self) -> None:
        # Emulate the kernel: with period=0 every duty_cycle write fails with EINVAL.
        from devices import pwm_dual

        original_write = pwm_dual.SysfsPwmChannel.write

        def kernel_like_write(channel, name, value, ignore_errors=False):
            if name == "duty_cycle" and channel.read("period") == "0":
                if ignore_errors:
                    return
                raise PwmControlError(f"{channel.spec.name}: write duty_cycle={value} failed: [Errno 22] Invalid argument")
            return original_write(channel, name, value, ignore_errors)

        (self.chip4 / "pwm0" / "period").write_text("100000")  # PWM4 was used before, PWM7 is fresh
        controller = self.make_controller()
        with unittest.mock.patch.object(pwm_dual.SysfsPwmChannel, "write", kernel_like_write):
            status = controller.start()
            self.assertIsNone(status["error"])
            controller.apply(enabled=True, duty=50, frequency_hz=1_000)

        self.assert_both("period", 1_000_000)
        self.assert_both("duty_cycle", 500_000)
        self.assert_both("enable", 1)

    def test_pinmux_written_for_both_channels_only_when_different(self) -> None:
        controller = self.make_controller()
        controller.apply(enabled=True, duty=10)
        expected = {(spec.pinmux_address, spec.pinmux_value) for spec in DEFAULT_CHANNELS}
        self.assertEqual(set(self.pinmux.writes), expected)
        self.assertIn((0xD401E098, 0xD042), expected)

        self.pinmux.writes.clear()
        controller.apply(duty=20)
        self.assertEqual(self.pinmux.writes, [])

    def test_duty_change_does_not_rewrite_period_or_toggle_enable(self) -> None:
        controller = self.make_controller()
        controller.apply(enabled=True, duty=50)
        # Mark the nodes so an unwanted rewrite would be visible.
        for chip in (self.chip4, self.chip7):
            (chip / "pwm0" / "period").write_text("100000\n")
            (chip / "pwm0" / "enable").write_text("1\n")

        controller.apply(duty=75)

        self.assert_both("duty_cycle", 75_000)
        for chip in (self.chip4, self.chip7):
            self.assertEqual((chip / "pwm0" / "period").read_text(), "100000\n")
            self.assertEqual((chip / "pwm0" / "enable").read_text(), "1\n")

    def test_frequency_change_reprograms_both(self) -> None:
        controller = self.make_controller()
        controller.apply(enabled=True, duty=50, frequency_hz=10_000)
        controller.apply(frequency_hz=20_000)

        self.assert_both("period", 50_000)
        self.assert_both("duty_cycle", 25_000)
        self.assert_both("enable", 1)

    def test_disable_and_zero_duty_turn_both_off(self) -> None:
        controller = self.make_controller()
        controller.apply(enabled=True, duty=60)
        controller.apply(enabled=False)
        self.assert_both("enable", 0)
        self.assert_both("duty_cycle", 0)

        controller.apply(enabled=True, duty=0)
        self.assert_both("enable", 0)

    def test_active_low_inverts_duty(self) -> None:
        controller = self.make_controller(active_low=True)
        controller.apply(enabled=True, duty=30)
        self.assert_both("duty_cycle", 70_000)

    def test_invalid_values_rejected_without_touching_hardware(self) -> None:
        controller = self.make_controller()
        controller.apply(enabled=True, duty=50)
        for kwargs in ({"duty": 101}, {"duty": -1}, {"duty": float("nan")}, {"frequency_hz": 10}, {"frequency_hz": 10_000_000}):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(ValueError):
                    controller.apply(**kwargs)
        self.assert_both("duty_cycle", 50_000)

    def test_failure_on_one_channel_turns_both_off(self) -> None:
        controller = self.make_controller()
        controller.apply(enabled=True, duty=50)

        def broken_reader(address: int) -> int:
            raise OSError("no /dev/mem")

        controller._pinmux_reader = broken_reader
        with self.assertRaises(PwmControlError):
            controller.apply(duty=80)

        self.assert_both("enable", 0)
        self.assert_both("duty_cycle", 0)
        status = controller.status()
        self.assertIsNotNone(status["error"])
        self.assertFalse(status["enabled"])
        self.assertTrue(status["outputs_identical"])

    def test_missing_chip_reports_error_instead_of_crashing_on_start(self) -> None:
        controller = DualPwmController(
            state_path=self.state_path,
            log_path=None,
            pwm_root=self.root / "no-such-dir",
            pinmux_reader=self.pinmux.read,
            pinmux_writer=self.pinmux.write,
        )
        status = controller.start()
        self.assertIn("not found", status["error"])
        self.assertEqual([ch["pwmchip"] for ch in status["channels"]], [None, None])

    def test_state_persisted_and_restored(self) -> None:
        controller = self.make_controller()
        controller.apply(enabled=True, duty=33, frequency_hz=5_000)
        saved = json.loads(self.state_path.read_text(encoding="utf-8"))
        self.assertEqual((saved["enabled"], saved["duty"], saved["frequency_hz"]), (True, 33.0, 5_000))

        controller.stop()
        self.assert_both("enable", 0)

        restored = self.make_controller()
        status = restored.start()
        self.assertEqual((status["enabled"], status["duty"], status["frequency_hz"]), (True, 33.0, 5_000))
        self.assert_both("period", 200_000)
        self.assert_both("duty_cycle", 66_000)
        self.assert_both("enable", 1)


class WorkerClientTests(unittest.TestCase):
    """7860 side (DualPwmClient) -> command file -> root worker -> both channels."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.chip4 = make_fake_chip(root, "pwmchip0")
        self.chip7 = make_fake_chip(root, "pwmchip1")
        pinmux = FakePinmux()
        self.controller = DualPwmController(
            state_path=root / "pwm_dual.json",
            log_path=None,
            chip_paths={"pwm4": self.chip4, "pwm7": self.chip7},
            pinmux_reader=pinmux.read,
            pinmux_writer=pinmux.write,
        )
        self.command_path = root / "pwm_dual_command.json"
        self.status_path = root / "pwm_dual_status.json"
        self.worker = DualPwmWorker(self.controller, command_path=self.command_path, status_path=self.status_path)
        self.client = DualPwmClient(command_path=self.command_path, status_path=self.status_path, ack_timeout_s=0)
        self.controller.start()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def assert_both(self, node: str, value: int) -> None:
        self.assertEqual(read_node(self.chip4, node), value, f"pwm4 {node}")
        self.assertEqual(read_node(self.chip7, node), value, f"pwm7 {node}")

    def test_client_offline_without_worker_status(self) -> None:
        status = self.client.status()
        self.assertFalse(status["connected"])
        self.assertIsNone(status["duty"])

    def test_set_duty_reaches_both_channels(self) -> None:
        self.client.set_duty(40, frequency_hz=1_000)
        self.worker.poll_once()

        self.assert_both("period", 1_000_000)
        self.assert_both("duty_cycle", 400_000)
        self.assert_both("enable", 1)
        status = self.client.status()
        self.assertTrue(status["connected"])
        self.assertEqual((status["duty"], status["frequency_hz"]), (40.0, 1_000))
        self.assertTrue(status["outputs_identical"])
        self.assertEqual(len(status["channels"]), 2)

    def test_duty_zero_disables_and_frequency_is_kept(self) -> None:
        self.client.set_duty(60, frequency_hz=2_000)
        self.worker.poll_once()
        self.client.set_duty(0)
        self.worker.poll_once()

        self.assert_both("enable", 0)
        self.assertEqual(self.client.status()["frequency_hz"], 2_000)

        self.client.set_duty(30)
        self.worker.poll_once()
        self.assert_both("period", 500_000)
        self.assert_both("duty_cycle", 150_000)
        self.assert_both("enable", 1)

    def test_duty_is_clamped_and_bad_frequency_rejected(self) -> None:
        status = self.client.set_duty(150)
        self.assertEqual(status["target_duty"], 100.0)
        with self.assertRaises(ValueError):
            self.client.set_duty(50, frequency_hz=5)

    def test_invalid_command_file_reports_error_and_keeps_output(self) -> None:
        self.client.set_duty(50)
        self.worker.poll_once()
        self.command_path.write_text(json.dumps({"duty": 500, "enabled": True}), encoding="utf-8")
        self.worker.poll_once(force_command=True)

        self.assert_both("duty_cycle", 50_000)
        self.assertIn("invalid command", self.client.status()["error"])

    def test_worker_applies_pending_command_on_start(self) -> None:
        self.command_path.write_text(json.dumps({"duty": 25, "enabled": True, "frequency_hz": 20_000}), encoding="utf-8")
        self.worker.poll_once(force_command=True)
        self.assert_both("period", 50_000)
        self.assert_both("duty_cycle", 12_500)


if __name__ == "__main__":
    unittest.main()
