"""Verify a real theme preference across a signed Alpha 80 -> 81 upgrade.

Requires an already booted emulator and verified APKs. Never uninstalls or
clears data. UI/screenshot assertions fail closed if semantics are unavailable.
This candidate has not yet been exercised against an emulator.
"""

import argparse
import io
import json
import re
import subprocess
import time
import xml.etree.ElementTree as ET
from pathlib import Path

from PIL import Image, ImageStat


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--adb', required=True)
parser.add_argument('--serial', default='emulator-5554')
parser.add_argument('--alpha80', type=Path, required=True)
mode = parser.add_mutually_exclusive_group(required=True)
mode.add_argument('--alpha81', type=Path)
mode.add_argument('--preflight-alpha80', action='store_true')
parser.add_argument('--output', type=Path, required=True)
args = parser.parse_args()
args.output.mkdir(parents=True, exist_ok=True)
package = 'ru.luxvelo.preview.test'
counter = 0


def adb(*command, binary=False):
    result = subprocess.run(
        [args.adb, '-s', args.serial, *map(str, command)],
        check=True, capture_output=True, timeout=180,
    )
    return result.stdout if binary else result.stdout.decode().strip()


def snapshot(label):
    global counter
    counter += 1
    prefix = args.output / f'{counter:03d}-{label}'
    png = adb('exec-out', 'screencap', '-p', binary=True)
    prefix.with_suffix('.png').write_bytes(png)
    adb('shell', 'uiautomator', 'dump', '/sdcard/luxvelo-check.xml')
    xml = adb('exec-out', 'cat', '/sdcard/luxvelo-check.xml')
    prefix.with_suffix('.xml').write_text(xml)
    return ET.fromstring(xml), png


def tap_label(label):
    deadline = time.monotonic() + 35
    while time.monotonic() < deadline:
        tree, _ = snapshot('find-label')
        parents = {child: node for node in tree.iter() for child in node}
        for node in tree.iter('node'):
            labels = (node.get('text', ''), node.get('content-desc', ''))
            if not any(label == value or label in value.splitlines() for value in labels):
                continue
            target = node
            while target.get('clickable') != 'true' and target in parents:
                target = parents[target]
            if target.get('clickable') != 'true':
                continue
            bounds = re.fullmatch(r'\[(\d+),(\d+)\]\[(\d+),(\d+)\]', target.get('bounds', ''))
            if not bounds:
                continue
            x1, y1, x2, y2 = map(int, bounds.groups())
            assert x2 > x1 and y2 > y1
            adb('shell', 'input', 'tap', (x1 + x2) // 2, (y1 + y2) // 2)
            time.sleep(1)
            return
        time.sleep(1)
    print(ET.tostring(tree, encoding='unicode'))
    raise AssertionError(f'Visible clickable UI label not found: {label}')


def launch_settings(version_code):
    adb('shell', 'am', 'force-stop', package)
    launch = adb('shell', 'am', 'start', '-W', '-n', package + '/ru.luxvelo.luxvelo.MainLauncher')
    assert 'Status: ok' in launch, launch
    assert adb('shell', 'pidof', package)
    dump = adb('shell', 'dumpsys', 'package', package)
    assert re.search(rf'\bversionCode={version_code}\b', dump)
    assert f'versionName=0.1.0-alpha.{version_code}' in dump
    time.sleep(3)
    tap_label('Меню')
    tap_label('Настройки')


def set_theme(label):
    tap_label('Тема')
    tap_label(label)


def verify_theme(dark, label):
    tree, png = snapshot(label)
    assert any('Проверка обновления' in (n.get('text', '') + n.get('content-desc', '')) for n in tree.iter('node'))
    image = Image.open(io.BytesIO(png)).convert('L')
    w, h = image.size
    median = ImageStat.Stat(image.crop((int(w*.15), int(h*.45), int(w*.85), int(h*.8)))).median[0]
    assert median < 90 if dark else median > 150, (label, median)
    return median


# The system stays light, so an accidentally lost preference cannot pass as dark.
adb('shell', 'cmd', 'uimode', 'night', 'no')
assert package not in adb('shell', 'pm', 'list', 'packages', package), 'Use a fresh emulator; never uninstall to make this test pass.'
assert 'Success' in adb('install', '--no-streaming', args.alpha80)
launch_settings(80)
set_theme('Светлая')
light = verify_theme(False, 'alpha80-light')
set_theme('Тёмная')
dark_before = verify_theme(True, 'alpha80-dark')
launch_settings(80)
dark_relaunch = verify_theme(True, 'alpha80-dark-after-relaunch')
if args.preflight_alpha80:
    report = {'scope': 'Alpha80 preflight only; no Alpha81 or upgrade tested', 'package': package, 'launch': 'ok', 'control_setting': 'theme_mode=dark chosen through UI', 'system_theme': 'light', 'setting_survived_alpha80_relaunch': True, 'light_luminance': light, 'dark_luminance': dark_before, 'dark_relaunch_luminance': dark_relaunch}
    (args.output / 'preflight-result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(report, ensure_ascii=False))
    raise SystemExit(0)
uid_before = adb('shell', 'pm', 'list', 'packages', '-U', package)
assert uid_before.startswith('package:' + package + ' uid:')
adb('shell', 'am', 'force-stop', package)
assert 'Success' in adb('install', '-r', '--no-streaming', args.alpha81)
uid_after = adb('shell', 'pm', 'list', 'packages', '-U', package)
assert uid_after == uid_before
launch_settings(81)
dark_after = verify_theme(True, 'alpha81-preserved-dark-theme')
report = {
    'upgrade': '80 -> 81', 'package': package, 'uid_preserved': True,
    'launch': 'ok', 'control_setting': 'theme_mode=dark chosen through app UI',
    'system_theme': 'light', 'setting_preserved': True,
    'screen_background_luminance': {
        'alpha80_light': light, 'alpha80_dark': dark_before,
        'alpha80_dark_relaunch': dark_relaunch, 'alpha81_dark': dark_after,
    },
}
(args.output / 'upgrade-result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
print(json.dumps(report, ensure_ascii=False))
