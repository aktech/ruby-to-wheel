"""ruby-to-wheel: Package pre-built Ruby binaries into Python wheels."""

import argparse
import base64
import csv
import hashlib
import io
import os
import platform as platform_mod
import stat
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

__version__ = "0.1.0"

# Platform key -> wheel platform tag
PLATFORM_TAGS: dict[str, str] = {
    "linux-amd64": "manylinux_2_17_x86_64",
    "linux-arm64": "manylinux_2_17_aarch64",
    "linux-amd64-musl": "musllinux_1_2_x86_64",
    "linux-arm64-musl": "musllinux_1_2_aarch64",
    "darwin-amd64": "macosx_10_9_x86_64",
    "darwin-arm64": "macosx_11_0_arm64",
    "windows-amd64": "win_amd64",
    "windows-arm64": "win_arm64",
}

# Filename suffix -> platform key (tried longest-first during matching)
FILENAME_PLATFORM_PATTERNS: dict[str, str] = {
    # Musl variants (longer suffixes)
    "linux-x86_64-musl": "linux-amd64-musl",
    "linux-amd64-musl": "linux-amd64-musl",
    "linux-aarch64-musl": "linux-arm64-musl",
    "linux-arm64-musl": "linux-arm64-musl",
    "x86_64-linux-musl": "linux-amd64-musl",
    "aarch64-linux-musl": "linux-arm64-musl",
    # GNU/Tebako triple patterns
    "x86_64-linux-gnu": "linux-amd64",
    "aarch64-linux-gnu": "linux-arm64",
    "x86_64-apple-darwin": "darwin-amd64",
    "aarch64-apple-darwin": "darwin-arm64",
    "arm64-apple-darwin": "darwin-arm64",
    "x86_64-pc-windows": "windows-amd64",
    "aarch64-pc-windows": "windows-arm64",
    # Standard patterns
    "linux-x86_64": "linux-amd64",
    "linux-amd64": "linux-amd64",
    "linux-aarch64": "linux-arm64",
    "linux-arm64": "linux-arm64",
    "darwin-x86_64": "darwin-amd64",
    "darwin-amd64": "darwin-amd64",
    "darwin-aarch64": "darwin-arm64",
    "darwin-arm64": "darwin-arm64",
    "windows-x86_64": "windows-amd64",
    "windows-amd64": "windows-amd64",
    "windows-aarch64": "windows-arm64",
    "windows-arm64": "windows-arm64",
}


def normalize_package_name(name: str) -> str:
    """Normalize package name for wheel filename (PEP 427)."""
    return name.replace("-", "_").replace(".", "_").lower()


def normalize_import_name(name: str) -> str:
    """Normalize package name for Python import (PEP 8)."""
    return name.replace("-", "_").replace(".", "_").lower()


def compute_file_hash(data: bytes) -> str:
    """Compute SHA256 hash in wheel RECORD format."""
    digest = hashlib.sha256(data).digest()
    encoded = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return f"sha256={encoded}"


def generate_init_py(version: str, binary_name: str) -> str:
    """Generate __init__.py content."""
    return f'''"""Ruby binary packaged as Python wheel."""

import os
import stat
import subprocess
import sys

__version__ = "{version}"


def get_binary_path():
    """Return the path to the bundled binary."""
    binary = os.path.join(os.path.dirname(__file__), "bin", "{binary_name}")

    # Ensure binary is executable on Unix
    if sys.platform != "win32":
        current_mode = os.stat(binary).st_mode
        if not (current_mode & stat.S_IXUSR):
            os.chmod(binary, current_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    return binary


def _setup_lib_path():
    """Add bundled shared libraries to the library search path."""
    lib_dir = os.path.join(os.path.dirname(__file__), "lib")
    if not os.path.isdir(lib_dir):
        return
    if sys.platform == "darwin":
        env_var = "DYLD_LIBRARY_PATH"
    else:
        env_var = "LD_LIBRARY_PATH"
    existing = os.environ.get(env_var, "")
    if existing:
        os.environ[env_var] = lib_dir + os.pathsep + existing
    else:
        os.environ[env_var] = lib_dir


def main():
    """Execute the bundled binary."""
    binary = get_binary_path()
    _setup_lib_path()

    if sys.platform == "win32":
        # On Windows, use subprocess to properly handle signals
        sys.exit(subprocess.call([binary] + sys.argv[1:]))
    else:
        # On Unix, exec replaces the process
        os.execvp(binary, [binary] + sys.argv[1:])
'''


def generate_main_py() -> str:
    """Generate __main__.py content."""
    return '''from . import main
main()
'''


def generate_metadata(
    name: str,
    version: str,
    description: str = "Ruby binary packaged as Python wheel",
    requires_python: str = ">=3.10",
    author: str | None = None,
    author_email: str | None = None,
    license_: str | None = None,
    url: str | None = None,
    readme_content: str | None = None,
) -> str:
    """Generate METADATA file content."""
    lines = [
        "Metadata-Version: 2.1",
        f"Name: {name}",
        f"Version: {version}",
        f"Summary: {description}",
    ]

    if author:
        lines.append(f"Author: {author}")
    if author_email:
        lines.append(f"Author-email: {author_email}")
    if license_:
        lines.append(f"License: {license_}")
    if url:
        lines.append(f"Home-page: {url}")

    lines.append(f"Requires-Python: {requires_python}")

    if readme_content:
        lines.append("Description-Content-Type: text/markdown")
        lines.append("")
        lines.append(readme_content)

    return "\n".join(lines) + "\n"


def generate_wheel_metadata(platform_tag: str) -> str:
    """Generate WHEEL file content."""
    return f"""Wheel-Version: 1.0
Generator: ruby-to-wheel {__version__}
Root-Is-Purelib: false
Tag: py3-none-{platform_tag}
"""


def generate_entry_points(entry_point: str, import_name: str) -> str:
    """Generate entry_points.txt content."""
    return f"""[console_scripts]
{entry_point} = {import_name}:main
"""


def generate_record(files: dict[str, bytes]) -> str:
    """Generate RECORD file content."""
    output = io.StringIO()
    writer = csv.writer(output)

    for path, content in files.items():
        if path.endswith("RECORD"):
            writer.writerow([path, "", ""])
        else:
            hash_val = compute_file_hash(content)
            writer.writerow([path, hash_val, len(content)])

    return output.getvalue()


def build_wheel(
    binary_path: str,
    output_dir: str,
    name: str,
    version: str,
    platform_tag: str,
    entry_point: str,
    is_windows: bool = False,
    description: str = "Ruby binary packaged as Python wheel",
    requires_python: str = ">=3.10",
    author: str | None = None,
    author_email: str | None = None,
    license_: str | None = None,
    url: str | None = None,
    readme_content: str | None = None,
    extra_libs: list[str] | None = None,
) -> str:
    """Build a wheel file from a pre-built binary."""
    normalized_name = normalize_package_name(name)
    import_name = normalize_import_name(name)
    binary_name = entry_point + (".exe" if is_windows else "")

    with open(binary_path, "rb") as f:
        binary_content = f.read()

    files: dict[str, bytes] = {}

    init_content = generate_init_py(version, binary_name).encode("utf-8")
    main_content = generate_main_py().encode("utf-8")

    files[f"{import_name}/__init__.py"] = init_content
    files[f"{import_name}/__main__.py"] = main_content
    files[f"{import_name}/bin/{binary_name}"] = binary_content

    for lib_path in extra_libs or []:
        lib_name = Path(lib_path).name
        with open(lib_path, "rb") as f:
            files[f"{import_name}/lib/{lib_name}"] = f.read()

    dist_info = f"{normalized_name}-{version}.dist-info"

    metadata_content = generate_metadata(
        name,
        version,
        description=description,
        requires_python=requires_python,
        author=author,
        author_email=author_email,
        license_=license_,
        url=url,
        readme_content=readme_content,
    ).encode("utf-8")

    wheel_content = generate_wheel_metadata(platform_tag).encode("utf-8")
    entry_points_content = generate_entry_points(entry_point, import_name).encode(
        "utf-8"
    )

    files[f"{dist_info}/METADATA"] = metadata_content
    files[f"{dist_info}/WHEEL"] = wheel_content
    files[f"{dist_info}/entry_points.txt"] = entry_points_content

    record_path = f"{dist_info}/RECORD"
    files[record_path] = b""
    record_content = generate_record(files).encode("utf-8")
    files[record_path] = record_content

    wheel_name = f"{normalized_name}-{version}-py3-none-{platform_tag}.whl"
    wheel_path = os.path.join(output_dir, wheel_name)

    with zipfile.ZipFile(wheel_path, "w", zipfile.ZIP_DEFLATED) as whl:
        for file_path, content in files.items():
            if "/bin/" in file_path or "/lib/" in file_path:
                info = zipfile.ZipInfo(file_path)
                # Set Unix permissions: rwxr-xr-x (0755)
                info.external_attr = (
                    stat.S_IRWXU
                    | stat.S_IRGRP
                    | stat.S_IXGRP
                    | stat.S_IROTH
                    | stat.S_IXOTH
                ) << 16
                whl.writestr(info, content)
            else:
                whl.writestr(file_path, content)

    return wheel_path


def detect_binaries_in_dir(directory: str, name: str) -> dict[str, str]:
    """Scan directory and match filenames to platform keys.

    Expected filename format: {name}[-_.]{platform_suffix}[.exe]
    """
    dir_path = Path(directory)
    if not dir_path.is_dir():
        raise FileNotFoundError(f"Binary directory not found: {directory}")

    # Sort patterns by length (longest first) to avoid ambiguous matches
    sorted_patterns = sorted(
        FILENAME_PLATFORM_PATTERNS.items(), key=lambda x: len(x[0]), reverse=True
    )

    binaries: dict[str, str] = {}

    for entry in sorted(dir_path.iterdir()):
        if not entry.is_file():
            continue

        filename = entry.name
        # Strip .exe suffix
        stem = filename.removesuffix(".exe")

        # Try stripping name prefix with various separators
        remainder = None
        for sep in ["-", "_", "."]:
            prefix = name + sep
            if stem.startswith(prefix):
                remainder = stem[len(prefix):]
                break

        if remainder is None:
            continue

        # Match against platform patterns (longest first)
        for suffix, platform_key in sorted_patterns:
            if remainder == suffix:
                binaries[platform_key] = str(entry)
                break

    if not binaries:
        raise ValueError(
            f"No binaries matching '{name}' found in {directory}. "
            f"Expected filenames like: {name}-linux-amd64, {name}-darwin-arm64, etc."
        )

    return binaries


def parse_binary_args(binary_args: list[str]) -> dict[str, str]:
    """Parse --binary PLATFORM=PATH arguments into a dict."""
    binaries: dict[str, str] = {}

    for arg in binary_args:
        if "=" not in arg:
            raise ValueError(
                f"Invalid --binary argument: {arg!r}. Expected format: PLATFORM=PATH"
            )

        platform_key, path = arg.split("=", 1)

        if platform_key not in PLATFORM_TAGS:
            raise ValueError(
                f"Unknown platform: {platform_key!r}. "
                f"Supported platforms: {', '.join(sorted(PLATFORM_TAGS))}"
            )

        if not Path(path).exists():
            raise FileNotFoundError(f"Binary not found: {path}")

        binaries[platform_key] = path

    if not binaries:
        raise ValueError("No binaries provided")

    return binaries


def build_wheels(
    binaries: dict[str, str],
    *,
    name: str,
    version: str = "0.1.0",
    output_dir: str = "./dist",
    entry_point: str | None = None,
    description: str = "Ruby binary packaged as Python wheel",
    requires_python: str = ">=3.10",
    author: str | None = None,
    author_email: str | None = None,
    license_: str | None = None,
    url: str | None = None,
    readme: str | None = None,
    extra_libs: list[str] | None = None,
) -> list[str]:
    """Build Python wheels from pre-built binaries.

    Args:
        binaries: Mapping of platform keys to binary file paths
        name: Python package name
        version: Package version
        output_dir: Directory to write wheels to
        entry_point: CLI command name (defaults to package name)
        description: Package description
        requires_python: Python version requirement
        author: Author name
        author_email: Author email
        license_: License identifier
        url: Project URL
        readme: Path to README markdown file
        extra_libs: Paths to shared libraries to bundle

    Returns:
        List of paths to built wheel files
    """
    if entry_point is None:
        entry_point = name

    readme_content: str | None = None
    if readme:
        readme_path = Path(readme)
        if not readme_path.exists():
            raise FileNotFoundError(f"README file not found: {readme}")
        readme_content = readme_path.read_text(encoding="utf-8")

    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    built_wheels: list[str] = []

    for platform_key, binary_path in binaries.items():
        if platform_key not in PLATFORM_TAGS:
            print(f"Warning: Unknown platform {platform_key}, skipping")
            continue

        platform_tag = PLATFORM_TAGS[platform_key]
        is_windows = platform_key.startswith("windows-")

        if not Path(binary_path).exists():
            raise FileNotFoundError(f"Binary not found: {binary_path}")

        wheel_path = build_wheel(
            binary_path,
            str(out_path),
            name,
            version,
            platform_tag,
            entry_point,
            is_windows=is_windows,
            description=description,
            requires_python=requires_python,
            author=author,
            author_email=author_email,
            license_=license_,
            url=url,
            readme_content=readme_content,
            extra_libs=extra_libs,
        )

        built_wheels.append(wheel_path)

    return built_wheels


def detect_current_platform() -> str:
    """Detect the current platform and return the platform key."""
    system = platform_mod.system().lower()
    machine = platform_mod.machine().lower()

    if system == "linux":
        os_name = "linux"
    elif system == "darwin":
        os_name = "darwin"
    elif system == "windows":
        os_name = "windows"
    else:
        raise RuntimeError(f"Unsupported operating system: {system}")

    if machine in ("x86_64", "amd64"):
        arch = "amd64"
    elif machine in ("aarch64", "arm64"):
        arch = "arm64"
    else:
        raise RuntimeError(f"Unsupported architecture: {machine}")

    return f"{os_name}-{arch}"


def build_with_tebako(
    source_dir: str,
    entry_point: str,
    output_path: str,
    ruby_version: str = "3.3.7",
) -> None:
    """Build a standalone binary from Ruby source using Tebako.

    Args:
        source_dir: Path to the Ruby project source directory
        entry_point: Path to the Ruby entry point file (relative to source_dir)
        output_path: Path for the output binary
        ruby_version: Ruby version to use for Tebako packaging

    Raises:
        RuntimeError: If the tebako build fails
    """
    cmd = [
        "tebako",
        "press",
        "-e",
        entry_point,
        "-r",
        source_dir,
        "-o",
        output_path,
        "-R",
        ruby_version,
    ]

    print(f"Running: {' '.join(cmd)}")

    result = subprocess.run(cmd)

    if result.returncode != 0:
        raise RuntimeError(
            f"Tebako build failed (exit code {result.returncode})"
        )


def build_wheels_from_source(
    source_dir: str,
    *,
    name: str,
    version: str = "0.1.0",
    output_dir: str = "./dist",
    entry_point: str | None = None,
    source_entry_point: str | None = None,
    ruby_version: str = "3.3.7",
    platform_key: str | None = None,
    description: str = "Ruby binary packaged as Python wheel",
    requires_python: str = ">=3.10",
    author: str | None = None,
    author_email: str | None = None,
    license_: str | None = None,
    url: str | None = None,
    readme: str | None = None,
    extra_libs: list[str] | None = None,
) -> list[str]:
    """Build Python wheels from Ruby source using Tebako.

    Args:
        source_dir: Path to the Ruby project source directory
        name: Python package name
        version: Package version
        output_dir: Directory to write wheels to
        entry_point: CLI command name (defaults to package name)
        source_entry_point: Ruby entry point file (defaults to bin/{entry_point})
        ruby_version: Ruby version for Tebako
        platform_key: Override platform detection (e.g., "linux-amd64")
        description: Package description
        requires_python: Python version requirement
        author: Author name
        author_email: Author email
        license_: License identifier
        url: Project URL
        readme: Path to README markdown file
        extra_libs: Paths to shared libraries to bundle

    Returns:
        List of paths to built wheel files
    """
    if entry_point is None:
        entry_point = name

    if platform_key is None:
        platform_key = detect_current_platform()

    if platform_key not in PLATFORM_TAGS:
        raise ValueError(
            f"Unknown platform: {platform_key!r}. "
            f"Supported platforms: {', '.join(sorted(PLATFORM_TAGS))}"
        )

    if source_entry_point is None:
        source_entry_point = f"bin/{entry_point}"

    print(f"Building for platform: {platform_key}")
    print(f"Ruby entry point: {source_entry_point}")
    print(f"Ruby version: {ruby_version}")

    with tempfile.TemporaryDirectory() as tmp_dir:
        binary_path = os.path.join(tmp_dir, entry_point)

        build_with_tebako(
            source_dir=source_dir,
            entry_point=source_entry_point,
            output_path=binary_path,
            ruby_version=ruby_version,
        )

        return build_wheels(
            {platform_key: binary_path},
            name=name,
            version=version,
            output_dir=output_dir,
            entry_point=entry_point,
            description=description,
            requires_python=requires_python,
            author=author,
            author_email=author_email,
            license_=license_,
            url=url,
            readme=readme,
            extra_libs=extra_libs,
        )


def main() -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        prog="ruby-to-wheel",
        description="Package pre-built Ruby binaries into Python wheels",
    )

    binary_group = parser.add_mutually_exclusive_group(required=True)
    binary_group.add_argument(
        "--binary",
        action="append",
        dest="binaries",
        metavar="PLATFORM=PATH",
        help=(
            "Binary for a specific platform (repeatable). "
            "E.g.: --binary linux-amd64=/path/to/binary"
        ),
    )
    binary_group.add_argument(
        "--binary-dir",
        help="Directory containing platform-named binaries for auto-detection",
    )
    binary_group.add_argument(
        "--source",
        help="Build from Ruby source directory using Tebako",
    )

    parser.add_argument(
        "--name",
        required=True,
        help="Python package name",
    )
    parser.add_argument(
        "--version",
        default="0.1.0",
        help="Package version (default: 0.1.0)",
    )
    parser.add_argument(
        "--output-dir",
        default="./dist",
        help="Directory for built wheels (default: ./dist)",
    )
    parser.add_argument(
        "--entry-point",
        help="CLI command name (defaults to package name)",
    )
    parser.add_argument(
        "--description",
        default="Ruby binary packaged as Python wheel",
        help="Package description",
    )
    parser.add_argument(
        "--requires-python",
        default=">=3.10",
        help="Python version requirement (default: >=3.10)",
    )
    parser.add_argument(
        "--author",
        help="Author name",
    )
    parser.add_argument(
        "--author-email",
        help="Author email",
    )
    parser.add_argument(
        "--license",
        dest="license_",
        help="License identifier",
    )
    parser.add_argument(
        "--url",
        help="Project URL",
    )
    parser.add_argument(
        "--readme",
        help="Path to README markdown file for PyPI long description",
    )
    parser.add_argument(
        "--ruby-version",
        default="3.3.7",
        help="Ruby version for Tebako (default: 3.3.7, only used with --source)",
    )
    parser.add_argument(
        "--source-entry-point",
        help="Ruby entry point file for Tebako (default: bin/{entry_point}, only used with --source)",
    )
    parser.add_argument(
        "--platform",
        help="Override platform detection (e.g., linux-amd64, only used with --source)",
    )
    parser.add_argument(
        "--extra-lib",
        action="append",
        dest="extra_libs",
        metavar="PATH",
        help="Shared library to bundle in the wheel (repeatable)",
    )

    args = parser.parse_args()

    print(f"ruby-to-wheel v{__version__}")

    try:
        if args.source:
            wheels = build_wheels_from_source(
                args.source,
                name=args.name,
                version=args.version,
                output_dir=args.output_dir,
                entry_point=args.entry_point,
                source_entry_point=args.source_entry_point,
                ruby_version=args.ruby_version,
                platform_key=args.platform,
                description=args.description,
                requires_python=args.requires_python,
                author=args.author,
                author_email=args.author_email,
                license_=args.license_,
                url=args.url,
                readme=args.readme,
                extra_libs=args.extra_libs,
            )
        elif args.binary_dir:
            binaries = detect_binaries_in_dir(args.binary_dir, args.name)
            print(f"Auto-detected {len(binaries)} binary(ies) in {args.binary_dir}")
            print()
            wheels = build_wheels(
                binaries,
                name=args.name,
                version=args.version,
                output_dir=args.output_dir,
                entry_point=args.entry_point,
                description=args.description,
                requires_python=args.requires_python,
                author=args.author,
                author_email=args.author_email,
                license_=args.license_,
                url=args.url,
                readme=args.readme,
                extra_libs=args.extra_libs,
            )
        else:
            binaries = parse_binary_args(args.binaries)
            print()
            wheels = build_wheels(
                binaries,
                name=args.name,
                version=args.version,
                output_dir=args.output_dir,
                entry_point=args.entry_point,
                description=args.description,
                requires_python=args.requires_python,
                author=args.author,
                author_email=args.author_email,
                license_=args.license_,
                url=args.url,
                readme=args.readme,
                extra_libs=args.extra_libs,
            )
    except (FileNotFoundError, ValueError, RuntimeError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    if not wheels:
        print("Error: No wheels were built", file=sys.stderr)
        return 1

    print(f"Built {len(wheels)} wheel(s):")
    for wheel in wheels:
        print(f"  {wheel}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
