# ruby-to-wheel

[![PyPI version](https://img.shields.io/pypi/v/ruby-to-wheel)](https://pypi.org/project/ruby-to-wheel/)
[![Test](https://github.com/aktech/ruby-to-wheel/actions/workflows/test.yml/badge.svg)](https://github.com/aktech/ruby-to-wheel/actions/workflows/test.yml)

Package pre-built Ruby binaries into Python wheels.

`ruby-to-wheel` takes compiled Ruby binaries and wraps them into platform-specific Python wheels with proper entry points, so they can be installed via `pip` and run as CLI commands.

## Installation

```bash
pip install ruby-to-wheel
```

## Usage

There are three modes of operation:

### 1. From pre-built binaries (explicit)

Provide binaries for each platform explicitly:

```bash
ruby-to-wheel \
  --name my-tool \
  --version 1.0.0 \
  --binary linux-amd64=/path/to/my-tool-linux-amd64 \
  --binary darwin-arm64=/path/to/my-tool-darwin-arm64
```

### 2. From a directory of binaries (auto-detect)

Point to a directory containing binaries with platform suffixes in their filenames:

```bash
ruby-to-wheel \
  --name my-tool \
  --version 1.0.0 \
  --binary-dir ./binaries/
```

Binaries are matched by filename patterns like `my-tool-linux-amd64`, `my-tool-darwin-arm64`, etc.

### 3. From Ruby source (via Tebako)

Build a standalone binary from Ruby source using [Tebako](https://github.com/tamatebako/tebako) and package it into a wheel:

```bash
ruby-to-wheel \
  --name my-tool \
  --version 1.0.0 \
  --source ./my-ruby-project/
```

## Supported Platforms

| Platform Key        | Wheel Tag                  |
|---------------------|----------------------------|
| `linux-amd64`       | `manylinux_2_17_x86_64`    |
| `linux-arm64`       | `manylinux_2_17_aarch64`   |
| `linux-amd64-musl`  | `musllinux_1_2_x86_64`     |
| `linux-arm64-musl`  | `musllinux_1_2_aarch64`    |
| `darwin-amd64`      | `macosx_10_9_x86_64`       |
| `darwin-arm64`      | `macosx_11_0_arm64`        |
| `windows-amd64`     | `win_amd64`                |
| `windows-arm64`     | `win_arm64`                |

## Options

```
--name              Python package name (required)
--version           Package version (default: 0.1.0)
--output-dir        Directory for built wheels (default: ./dist)
--entry-point       CLI command name (defaults to package name)
--description       Package description
--requires-python   Python version requirement (default: >=3.10)
--author            Author name
--author-email      Author email
--license           License identifier
--url               Project URL
--readme            Path to README for PyPI long description
--extra-lib         Shared library to bundle (repeatable)
--ruby-version      Ruby version for Tebako (default: 3.3.7)
--source-entry-point  Ruby entry point for Tebako (default: bin/{entry-point})
--platform          Override platform detection (for --source mode)
```

## How It Works

Each generated wheel contains:

- A Python package with `__init__.py` and `__main__.py`
- The compiled binary in a `bin/` subdirectory
- Optional shared libraries in a `lib/` subdirectory
- A console script entry point that delegates to the binary

When installed, `pip` creates a wrapper script so the binary can be invoked directly by name from the command line.

## See Also

- [go-to-wheel](https://github.com/simonw/go-to-wheel) - The Go equivalent that inspired this tool
- [maturin](https://github.com/PyO3/maturin) - The Rust equivalent
- [pip-binary-factory](https://github.com/ports-fwd/pip-binary-factory) - Template for packaging pre-built binaries

## License

Apache-2.0
