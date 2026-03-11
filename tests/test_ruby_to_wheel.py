"""Tests for ruby-to-wheel."""

import hashlib
import os
import platform
import stat
import subprocess
import sys
import zipfile
from unittest import mock

import pytest

from ruby_to_wheel import (
    FILENAME_PLATFORM_PATTERNS,
    PLATFORM_TAGS,
    build_wheel,
    build_wheels,
    build_wheels_from_source,
    build_with_tebako,
    compute_file_hash,
    detect_binaries_in_dir,
    detect_current_platform,
    generate_entry_points,
    generate_init_py,
    generate_main_py,
    generate_metadata,
    generate_record,
    generate_wheel_metadata,
    normalize_import_name,
    normalize_package_name,
    parse_binary_args,
)


def create_fake_binary(path, message="hello from ruby-to-wheel"):
    """Write a fake shell-script binary and make it executable."""
    path.write_text(f'#!/bin/sh\necho "{message}" "$@"\n')
    path.chmod(0o755)


def create_ruby_binary(path, message="hello from ruby-to-wheel"):
    """Write a real Ruby script binary and make it executable."""
    path.write_text(
        f'#!/usr/bin/env ruby\nputs "{message} " + ARGV.join(" ")\n'
    )
    path.chmod(0o755)


def get_current_platform_key():
    """Return the ruby-to-wheel platform key for the current machine, or None."""
    system = platform.system().lower()
    machine = platform.machine().lower()

    if system == "linux":
        os_name = "linux"
    elif system == "darwin":
        os_name = "darwin"
    else:
        return None

    if machine in ("x86_64", "amd64"):
        arch = "amd64"
    elif machine in ("aarch64", "arm64"):
        arch = "arm64"
    else:
        return None

    return f"{os_name}-{arch}"


# ---------------------------------------------------------------------------
# TestNormalization
# ---------------------------------------------------------------------------


class TestNormalization:
    def test_package_name_dashes(self):
        assert normalize_package_name("my-tool") == "my_tool"

    def test_package_name_dots(self):
        assert normalize_package_name("my.tool") == "my_tool"

    def test_package_name_uppercase(self):
        assert normalize_package_name("My-Tool") == "my_tool"

    def test_package_name_mixed(self):
        assert normalize_package_name("My-Cool.Tool") == "my_cool_tool"

    def test_import_name_same_as_package(self):
        assert normalize_import_name("my-tool") == "my_tool"

    def test_already_normalized(self):
        assert normalize_package_name("my_tool") == "my_tool"


# ---------------------------------------------------------------------------
# TestDetectBinariesInDir
# ---------------------------------------------------------------------------


class TestDetectBinariesInDir:
    def test_standard_naming(self, tmp_path):
        create_fake_binary(tmp_path / "mytool-linux-amd64")
        create_fake_binary(tmp_path / "mytool-darwin-arm64")

        result = detect_binaries_in_dir(str(tmp_path), "mytool")
        assert set(result.keys()) == {"linux-amd64", "darwin-arm64"}

    def test_underscore_separator(self, tmp_path):
        create_fake_binary(tmp_path / "mytool_linux-amd64")

        result = detect_binaries_in_dir(str(tmp_path), "mytool")
        assert "linux-amd64" in result

    def test_dot_separator(self, tmp_path):
        create_fake_binary(tmp_path / "mytool.linux-amd64")

        result = detect_binaries_in_dir(str(tmp_path), "mytool")
        assert "linux-amd64" in result

    def test_tebako_triple_naming(self, tmp_path):
        create_fake_binary(tmp_path / "mytool-x86_64-apple-darwin")
        create_fake_binary(tmp_path / "mytool-aarch64-linux-gnu")

        result = detect_binaries_in_dir(str(tmp_path), "mytool")
        assert set(result.keys()) == {"darwin-amd64", "linux-arm64"}

    def test_musl_detection(self, tmp_path):
        create_fake_binary(tmp_path / "mytool-linux-x86_64-musl")
        create_fake_binary(tmp_path / "mytool-linux-aarch64-musl")

        result = detect_binaries_in_dir(str(tmp_path), "mytool")
        assert set(result.keys()) == {"linux-amd64-musl", "linux-arm64-musl"}

    def test_exe_suffix_stripped(self, tmp_path):
        create_fake_binary(tmp_path / "mytool-windows-amd64.exe")

        result = detect_binaries_in_dir(str(tmp_path), "mytool")
        assert "windows-amd64" in result

    def test_no_matches_raises(self, tmp_path):
        (tmp_path / "unrelated-file").write_text("nope")

        with pytest.raises(ValueError, match="No binaries matching"):
            detect_binaries_in_dir(str(tmp_path), "mytool")

    def test_directory_not_found(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            detect_binaries_in_dir(str(tmp_path / "nonexistent"), "mytool")

    def test_ignores_non_matching_files(self, tmp_path):
        create_fake_binary(tmp_path / "mytool-linux-amd64")
        (tmp_path / "README.md").write_text("readme")
        (tmp_path / "other-linux-amd64").write_text("wrong name")

        result = detect_binaries_in_dir(str(tmp_path), "mytool")
        assert len(result) == 1
        assert "linux-amd64" in result

    def test_all_standard_platforms(self, tmp_path):
        for platform_key in PLATFORM_TAGS:
            create_fake_binary(tmp_path / f"mytool-{platform_key}")

        result = detect_binaries_in_dir(str(tmp_path), "mytool")
        assert set(result.keys()) == set(PLATFORM_TAGS.keys())


# ---------------------------------------------------------------------------
# TestParseBinaryArgs
# ---------------------------------------------------------------------------


class TestParseBinaryArgs:
    def test_single_binary(self, tmp_path):
        binary = tmp_path / "mybin"
        create_fake_binary(binary)

        result = parse_binary_args([f"linux-amd64={binary}"])
        assert result == {"linux-amd64": str(binary)}

    def test_multiple_binaries(self, tmp_path):
        b1 = tmp_path / "bin1"
        b2 = tmp_path / "bin2"
        create_fake_binary(b1)
        create_fake_binary(b2)

        result = parse_binary_args([f"linux-amd64={b1}", f"darwin-arm64={b2}"])
        assert set(result.keys()) == {"linux-amd64", "darwin-arm64"}

    def test_missing_equals(self):
        with pytest.raises(ValueError, match="Invalid --binary argument"):
            parse_binary_args(["linux-amd64/path/to/binary"])

    def test_unknown_platform(self, tmp_path):
        binary = tmp_path / "mybin"
        create_fake_binary(binary)

        with pytest.raises(ValueError, match="Unknown platform"):
            parse_binary_args([f"freebsd-amd64={binary}"])

    def test_missing_binary_file(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="Binary not found"):
            parse_binary_args([f"linux-amd64={tmp_path / 'nonexistent'}"])

    def test_empty_list(self):
        with pytest.raises(ValueError, match="No binaries provided"):
            parse_binary_args([])


# ---------------------------------------------------------------------------
# TestBuildWheel
# ---------------------------------------------------------------------------


class TestBuildWheel:
    def test_wheel_structure(self, tmp_path):
        binary = tmp_path / "mybin"
        create_fake_binary(binary)
        output_dir = tmp_path / "dist"
        output_dir.mkdir()

        wheel_path = build_wheel(
            str(binary),
            str(output_dir),
            name="my-tool",
            version="1.0.0",
            platform_tag="manylinux_2_17_x86_64",
            entry_point="my-tool",
        )

        assert os.path.exists(wheel_path)
        assert wheel_path.endswith(
            "my_tool-1.0.0-py3-none-manylinux_2_17_x86_64.whl"
        )

        with zipfile.ZipFile(wheel_path) as whl:
            names = whl.namelist()
            assert "my_tool/__init__.py" in names
            assert "my_tool/__main__.py" in names
            assert "my_tool/bin/my-tool" in names
            assert "my_tool-1.0.0.dist-info/METADATA" in names
            assert "my_tool-1.0.0.dist-info/WHEEL" in names
            assert "my_tool-1.0.0.dist-info/entry_points.txt" in names
            assert "my_tool-1.0.0.dist-info/RECORD" in names

    def test_binary_permissions(self, tmp_path):
        binary = tmp_path / "mybin"
        create_fake_binary(binary)
        output_dir = tmp_path / "dist"
        output_dir.mkdir()

        wheel_path = build_wheel(
            str(binary),
            str(output_dir),
            name="my-tool",
            version="1.0.0",
            platform_tag="manylinux_2_17_x86_64",
            entry_point="my-tool",
        )

        with zipfile.ZipFile(wheel_path) as whl:
            info = whl.getinfo("my_tool/bin/my-tool")
            unix_mode = (info.external_attr >> 16) & 0o777
            assert unix_mode == 0o755

    def test_windows_binary_name(self, tmp_path):
        binary = tmp_path / "mybin"
        create_fake_binary(binary)
        output_dir = tmp_path / "dist"
        output_dir.mkdir()

        wheel_path = build_wheel(
            str(binary),
            str(output_dir),
            name="my-tool",
            version="1.0.0",
            platform_tag="win_amd64",
            entry_point="my-tool",
            is_windows=True,
        )

        with zipfile.ZipFile(wheel_path) as whl:
            names = whl.namelist()
            assert "my_tool/bin/my-tool.exe" in names

    def test_metadata_content(self, tmp_path):
        binary = tmp_path / "mybin"
        create_fake_binary(binary)
        output_dir = tmp_path / "dist"
        output_dir.mkdir()

        wheel_path = build_wheel(
            str(binary),
            str(output_dir),
            name="my-tool",
            version="2.0.0",
            platform_tag="macosx_11_0_arm64",
            entry_point="my-tool",
            author="Test Author",
            license_="MIT",
        )

        with zipfile.ZipFile(wheel_path) as whl:
            metadata = whl.read("my_tool-2.0.0.dist-info/METADATA").decode()
            assert "Name: my-tool" in metadata
            assert "Version: 2.0.0" in metadata
            assert "Author: Test Author" in metadata
            assert "License: MIT" in metadata

    def test_wheel_metadata_content(self, tmp_path):
        binary = tmp_path / "mybin"
        create_fake_binary(binary)
        output_dir = tmp_path / "dist"
        output_dir.mkdir()

        wheel_path = build_wheel(
            str(binary),
            str(output_dir),
            name="my-tool",
            version="1.0.0",
            platform_tag="macosx_11_0_arm64",
            entry_point="my-tool",
        )

        with zipfile.ZipFile(wheel_path) as whl:
            wheel_meta = whl.read("my_tool-1.0.0.dist-info/WHEEL").decode()
            assert "Generator: ruby-to-wheel" in wheel_meta
            assert "Tag: py3-none-macosx_11_0_arm64" in wheel_meta
            assert "Root-Is-Purelib: false" in wheel_meta

    def test_record_hashes_valid(self, tmp_path):
        binary = tmp_path / "mybin"
        create_fake_binary(binary)
        output_dir = tmp_path / "dist"
        output_dir.mkdir()

        wheel_path = build_wheel(
            str(binary),
            str(output_dir),
            name="my-tool",
            version="1.0.0",
            platform_tag="manylinux_2_17_x86_64",
            entry_point="my-tool",
        )

        with zipfile.ZipFile(wheel_path) as whl:
            record = whl.read("my_tool-1.0.0.dist-info/RECORD").decode()

            for line in record.strip().split("\n"):
                parts = line.split(",")
                filepath = parts[0]
                if filepath.endswith("RECORD"):
                    # RECORD itself has no hash
                    assert parts[1] == ""
                    continue

                expected_hash = parts[1]
                expected_size = int(parts[2])
                content = whl.read(filepath)
                assert len(content) == expected_size
                assert compute_file_hash(content) == expected_hash

    def test_readme_in_metadata(self, tmp_path):
        binary = tmp_path / "mybin"
        create_fake_binary(binary)
        output_dir = tmp_path / "dist"
        output_dir.mkdir()

        wheel_path = build_wheel(
            str(binary),
            str(output_dir),
            name="my-tool",
            version="1.0.0",
            platform_tag="manylinux_2_17_x86_64",
            entry_point="my-tool",
            readme_content="# My Tool\n\nA great tool.",
        )

        with zipfile.ZipFile(wheel_path) as whl:
            metadata = whl.read("my_tool-1.0.0.dist-info/METADATA").decode()
            assert "Description-Content-Type: text/markdown" in metadata
            assert "# My Tool" in metadata


# ---------------------------------------------------------------------------
# TestBuildWheels
# ---------------------------------------------------------------------------


class TestBuildWheels:
    def test_multiple_platforms(self, tmp_path):
        b1 = tmp_path / "bin1"
        b2 = tmp_path / "bin2"
        create_fake_binary(b1)
        create_fake_binary(b2)
        output_dir = tmp_path / "dist"

        wheels = build_wheels(
            {"linux-amd64": str(b1), "darwin-arm64": str(b2)},
            name="my-tool",
            version="1.0.0",
            output_dir=str(output_dir),
        )

        assert len(wheels) == 2
        filenames = [os.path.basename(w) for w in wheels]
        assert any("manylinux_2_17_x86_64" in f for f in filenames)
        assert any("macosx_11_0_arm64" in f for f in filenames)

    def test_entry_point_defaults_to_name(self, tmp_path):
        binary = tmp_path / "mybin"
        create_fake_binary(binary)
        output_dir = tmp_path / "dist"

        wheels = build_wheels(
            {"linux-amd64": str(binary)},
            name="my-tool",
            version="1.0.0",
            output_dir=str(output_dir),
        )

        with zipfile.ZipFile(wheels[0]) as whl:
            ep = whl.read("my_tool-1.0.0.dist-info/entry_points.txt").decode()
            assert "my-tool = my_tool:main" in ep

    def test_custom_entry_point(self, tmp_path):
        binary = tmp_path / "mybin"
        create_fake_binary(binary)
        output_dir = tmp_path / "dist"

        wheels = build_wheels(
            {"linux-amd64": str(binary)},
            name="my-tool",
            version="1.0.0",
            output_dir=str(output_dir),
            entry_point="custom-cmd",
        )

        with zipfile.ZipFile(wheels[0]) as whl:
            ep = whl.read("my_tool-1.0.0.dist-info/entry_points.txt").decode()
            assert "custom-cmd = my_tool:main" in ep
            assert "my_tool/bin/custom-cmd" in whl.namelist()

    def test_readme_file(self, tmp_path):
        binary = tmp_path / "mybin"
        create_fake_binary(binary)
        readme = tmp_path / "README.md"
        readme.write_text("# Hello\n")
        output_dir = tmp_path / "dist"

        wheels = build_wheels(
            {"linux-amd64": str(binary)},
            name="my-tool",
            version="1.0.0",
            output_dir=str(output_dir),
            readme=str(readme),
        )

        with zipfile.ZipFile(wheels[0]) as whl:
            metadata = whl.read("my_tool-1.0.0.dist-info/METADATA").decode()
            assert "# Hello" in metadata

    def test_missing_readme_raises(self, tmp_path):
        binary = tmp_path / "mybin"
        create_fake_binary(binary)

        with pytest.raises(FileNotFoundError, match="README file not found"):
            build_wheels(
                {"linux-amd64": str(binary)},
                name="my-tool",
                version="1.0.0",
                readme=str(tmp_path / "nonexistent.md"),
            )

    def test_missing_binary_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="Binary not found"):
            build_wheels(
                {"linux-amd64": str(tmp_path / "nonexistent")},
                name="my-tool",
                version="1.0.0",
            )


# ---------------------------------------------------------------------------
# TestWheelExecution — end-to-end
# ---------------------------------------------------------------------------


class TestWheelExecution:
    @pytest.fixture()
    def current_platform(self):
        key = get_current_platform_key()
        if key is None:
            pytest.skip("Unsupported platform for E2E test")
        return key

    @pytest.fixture()
    def uv_available(self):
        result = subprocess.run(
            ["uv", "--version"], capture_output=True, text=True
        )
        if result.returncode != 0:
            pytest.skip("uv not available")

    @pytest.fixture()
    def ruby_available(self):
        result = subprocess.run(
            ["ruby", "--version"], capture_output=True, text=True
        )
        if result.returncode != 0:
            pytest.skip("ruby not available")

    def test_ruby_entry_point_execution(
        self, tmp_path, current_platform, uv_available, ruby_available
    ):
        binary = tmp_path / "hello-ruby"
        create_ruby_binary(binary, message="hello from ruby-to-wheel")

        output_dir = tmp_path / "dist"
        wheels = build_wheels(
            {current_platform: str(binary)},
            name="hello-ruby",
            version="0.1.0",
            output_dir=str(output_dir),
        )
        assert len(wheels) == 1

        venv_dir = tmp_path / "venv"
        subprocess.run(
            ["uv", "venv", str(venv_dir)], check=True, capture_output=True
        )

        python = str(venv_dir / "bin" / "python")
        subprocess.run(
            ["uv", "pip", "install", wheels[0], "--python", python],
            check=True,
            capture_output=True,
        )

        # Test entry point script runs a real Ruby interpreter
        result = subprocess.run(
            [str(venv_dir / "bin" / "hello-ruby"), "arg1", "arg2"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert "hello from ruby-to-wheel" in result.stdout
        assert "arg1 arg2" in result.stdout

    def test_ruby_python_m_execution(
        self, tmp_path, current_platform, uv_available, ruby_available
    ):
        binary = tmp_path / "hello-ruby"
        create_ruby_binary(binary, message="ruby module mode works")

        output_dir = tmp_path / "dist"
        wheels = build_wheels(
            {current_platform: str(binary)},
            name="hello-ruby",
            version="0.1.0",
            output_dir=str(output_dir),
        )

        venv_dir = tmp_path / "venv"
        subprocess.run(
            ["uv", "venv", str(venv_dir)], check=True, capture_output=True
        )

        python = str(venv_dir / "bin" / "python")
        subprocess.run(
            ["uv", "pip", "install", wheels[0], "--python", python],
            check=True,
            capture_output=True,
        )

        # Test python -m execution with real Ruby script
        result = subprocess.run(
            [python, "-m", "hello_ruby", "arg1"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert "ruby module mode works" in result.stdout
        assert "arg1" in result.stdout

    def test_ruby_multifile_project(
        self, tmp_path, current_platform, uv_available, ruby_available
    ):
        """Package a Ruby project with multiple files as a wheel."""
        # Create a small Ruby project structure
        project_dir = tmp_path / "myproject"
        lib_dir = project_dir / "lib"
        lib_dir.mkdir(parents=True)
        bin_dir = project_dir / "bin"
        bin_dir.mkdir()

        # lib/greeter.rb — a Ruby module
        (lib_dir / "greeter.rb").write_text(
            'module Greeter\n'
            '  def self.greet(name)\n'
            '    "Hello, #{name}! From Ruby #{RUBY_VERSION}"\n'
            '  end\n'
            'end\n'
        )

        # bin/greet — executable entry point that uses the lib
        entry = bin_dir / "greet"
        entry.write_text(
            '#!/usr/bin/env ruby\n'
            'require_relative "../lib/greeter"\n'
            'puts Greeter.greet(ARGV[0] || "World")\n'
        )
        entry.chmod(0o755)

        # Package the entry point script as the binary
        output_dir = tmp_path / "dist"
        wheels = build_wheels(
            {current_platform: str(entry)},
            name="greet",
            version="0.2.0",
            output_dir=str(output_dir),
        )
        assert len(wheels) == 1

        # The wheel packages just the entry script as a binary.
        # For a standalone binary you'd use Tebako (--source mode).
        # Here we verify the wheel structure is correct.
        with zipfile.ZipFile(wheels[0]) as whl:
            names = whl.namelist()
            assert "greet/__init__.py" in names
            assert "greet/bin/greet" in names

            # Verify the packaged binary is the real Ruby script
            content = whl.read("greet/bin/greet").decode()
            assert "#!/usr/bin/env ruby" in content
            assert "Greeter.greet" in content


# ---------------------------------------------------------------------------
# TestErrorHandling
# ---------------------------------------------------------------------------


class TestErrorHandling:
    def test_build_wheels_missing_binary(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="Binary not found"):
            build_wheels(
                {"linux-amd64": str(tmp_path / "nope")},
                name="test",
                version="0.1.0",
            )

    def test_detect_empty_dir(self, tmp_path):
        with pytest.raises(ValueError, match="No binaries matching"):
            detect_binaries_in_dir(str(tmp_path), "mytool")

    def test_detect_nonexistent_dir(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            detect_binaries_in_dir(str(tmp_path / "nope"), "mytool")


# ---------------------------------------------------------------------------
# TestCLI
# ---------------------------------------------------------------------------


class TestCLI:
    def test_help(self):
        result = subprocess.run(
            [sys.executable, "-m", "ruby_to_wheel", "--help"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert "ruby-to-wheel" in result.stdout
        assert "--binary" in result.stdout
        assert "--binary-dir" in result.stdout

    def test_missing_required_args(self):
        result = subprocess.run(
            [sys.executable, "-m", "ruby_to_wheel"],
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0

    def test_mutually_exclusive(self, tmp_path):
        binary = tmp_path / "mybin"
        create_fake_binary(binary)

        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "ruby_to_wheel",
                "--name",
                "test",
                "--binary",
                f"linux-amd64={binary}",
                "--binary-dir",
                str(tmp_path),
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0

    def test_cli_build_wheel(self, tmp_path):
        binary = tmp_path / "mybin"
        create_fake_binary(binary)
        output_dir = tmp_path / "dist"

        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "ruby_to_wheel",
                "--name",
                "cli-test",
                "--version",
                "0.2.0",
                "--binary",
                f"linux-amd64={binary}",
                "--output-dir",
                str(output_dir),
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert "Built 1 wheel" in result.stdout

        wheels = list(output_dir.glob("*.whl"))
        assert len(wheels) == 1
        assert "cli_test-0.2.0" in wheels[0].name

    def test_cli_binary_dir(self, tmp_path):
        binaries_dir = tmp_path / "binaries"
        binaries_dir.mkdir()
        create_fake_binary(binaries_dir / "mytool-linux-amd64")
        create_fake_binary(binaries_dir / "mytool-darwin-arm64")
        output_dir = tmp_path / "dist"

        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "ruby_to_wheel",
                "--name",
                "mytool",
                "--version",
                "1.0.0",
                "--binary-dir",
                str(binaries_dir),
                "--output-dir",
                str(output_dir),
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert "Built 2 wheel" in result.stdout

    def test_cli_unknown_binary_platform(self, tmp_path):
        binary = tmp_path / "mybin"
        create_fake_binary(binary)

        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "ruby_to_wheel",
                "--name",
                "test",
                "--binary",
                f"freebsd-amd64={binary}",
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0
        assert "Unknown platform" in result.stderr

    def test_cli_source_flag_in_help(self):
        result = subprocess.run(
            [sys.executable, "-m", "ruby_to_wheel", "--help"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert "--source" in result.stdout

    def test_cli_source_mutually_exclusive_with_binary(self, tmp_path):
        binary = tmp_path / "mybin"
        create_fake_binary(binary)

        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "ruby_to_wheel",
                "--name",
                "test",
                "--binary",
                f"linux-amd64={binary}",
                "--source",
                str(tmp_path),
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0

    def test_cli_source_mutually_exclusive_with_binary_dir(self, tmp_path):
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "ruby_to_wheel",
                "--name",
                "test",
                "--binary-dir",
                str(tmp_path),
                "--source",
                str(tmp_path),
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0


# ---------------------------------------------------------------------------
# TestDetectCurrentPlatform
# ---------------------------------------------------------------------------


class TestDetectCurrentPlatform:
    def test_returns_valid_platform_key(self):
        result = detect_current_platform()
        assert result in PLATFORM_TAGS

    def test_matches_system_info(self):
        result = detect_current_platform()
        system = platform.system().lower()
        assert result.startswith(system if system != "windows" else "windows")

    def test_unsupported_os(self):
        with mock.patch("ruby_to_wheel.platform_mod") as mock_platform:
            mock_platform.system.return_value = "FreeBSD"
            mock_platform.machine.return_value = "x86_64"
            with pytest.raises(RuntimeError, match="Unsupported operating system"):
                detect_current_platform()

    def test_unsupported_arch(self):
        with mock.patch("ruby_to_wheel.platform_mod") as mock_platform:
            mock_platform.system.return_value = "Linux"
            mock_platform.machine.return_value = "mips"
            with pytest.raises(RuntimeError, match="Unsupported architecture"):
                detect_current_platform()


# ---------------------------------------------------------------------------
# TestBuildWithTebako
# ---------------------------------------------------------------------------


class TestBuildWithTebako:
    def test_correct_command(self):
        with mock.patch("ruby_to_wheel.subprocess.run") as mock_run:
            mock_run.return_value = mock.Mock(returncode=0)
            build_with_tebako(
                source_dir="/src",
                entry_point="bin/kamal",
                output_path="/out/kamal",
                ruby_version="3.3.7",
            )
            mock_run.assert_called_once_with(
                [
                    "tebako",
                    "press",
                    "-e",
                    "bin/kamal",
                    "-r",
                    "/src",
                    "-o",
                    "/out/kamal",
                    "-R",
                    "3.3.7",
                ],
            )

    def test_custom_ruby_version(self):
        with mock.patch("ruby_to_wheel.subprocess.run") as mock_run:
            mock_run.return_value = mock.Mock(returncode=0)
            build_with_tebako(
                source_dir="/src",
                entry_point="bin/app",
                output_path="/out/app",
                ruby_version="3.2.0",
            )
            cmd = mock_run.call_args[0][0]
            assert "-R" in cmd
            assert cmd[cmd.index("-R") + 1] == "3.2.0"

    def test_failure_raises_runtime_error(self):
        with mock.patch("ruby_to_wheel.subprocess.run") as mock_run:
            mock_run.return_value = mock.Mock(returncode=1)
            with pytest.raises(RuntimeError, match="Tebako build failed"):
                build_with_tebako(
                    source_dir="/src",
                    entry_point="bin/app",
                    output_path="/out/app",
                )


# ---------------------------------------------------------------------------
# TestBuildWheelsFromSource
# ---------------------------------------------------------------------------


class TestBuildWheelsFromSource:
    def test_builds_wheel_for_current_platform(self, tmp_path):
        output_dir = tmp_path / "dist"

        def fake_tebako(cmd):
            # Write a fake binary at the output path
            output_path = cmd[cmd.index("-o") + 1]
            with open(output_path, "w") as f:
                f.write("#!/bin/sh\necho hello\n")
            os.chmod(output_path, 0o755)
            return mock.Mock(returncode=0)

        with mock.patch("ruby_to_wheel.subprocess.run", side_effect=fake_tebako):
            wheels = build_wheels_from_source(
                str(tmp_path),
                name="myapp",
                version="1.0.0",
                output_dir=str(output_dir),
                platform_key="linux-amd64",
            )

        assert len(wheels) == 1
        assert "manylinux_2_17_x86_64" in os.path.basename(wheels[0])

    def test_default_source_entry_point(self, tmp_path):
        output_dir = tmp_path / "dist"

        def fake_tebako(cmd):
            output_path = cmd[cmd.index("-o") + 1]
            with open(output_path, "w") as f:
                f.write("#!/bin/sh\necho hello\n")
            os.chmod(output_path, 0o755)
            entry = cmd[cmd.index("-e") + 1]
            assert entry == "bin/myapp"
            return mock.Mock(returncode=0)

        with mock.patch("ruby_to_wheel.subprocess.run", side_effect=fake_tebako):
            build_wheels_from_source(
                str(tmp_path),
                name="myapp",
                version="1.0.0",
                output_dir=str(output_dir),
                platform_key="linux-amd64",
            )

    def test_custom_source_entry_point(self, tmp_path):
        output_dir = tmp_path / "dist"

        def fake_tebako(cmd):
            output_path = cmd[cmd.index("-o") + 1]
            with open(output_path, "w") as f:
                f.write("#!/bin/sh\necho hello\n")
            os.chmod(output_path, 0o755)
            entry = cmd[cmd.index("-e") + 1]
            assert entry == "exe/custom"
            return mock.Mock(returncode=0)

        with mock.patch("ruby_to_wheel.subprocess.run", side_effect=fake_tebako):
            build_wheels_from_source(
                str(tmp_path),
                name="myapp",
                version="1.0.0",
                output_dir=str(output_dir),
                source_entry_point="exe/custom",
                platform_key="linux-amd64",
            )

    def test_unknown_platform_raises(self, tmp_path):
        with pytest.raises(ValueError, match="Unknown platform"):
            build_wheels_from_source(
                str(tmp_path),
                name="myapp",
                version="1.0.0",
                platform_key="freebsd-amd64",
            )

    def test_tebako_failure_propagates(self, tmp_path):
        with mock.patch("ruby_to_wheel.subprocess.run") as mock_run:
            mock_run.return_value = mock.Mock(returncode=1)
            with pytest.raises(RuntimeError, match="Tebako build failed"):
                build_wheels_from_source(
                    str(tmp_path),
                    name="myapp",
                    version="1.0.0",
                    platform_key="linux-amd64",
                )
