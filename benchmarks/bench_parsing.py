"""Performance benchmarks for duho argument parsing."""

import timeit
import typing as ty
from duho import Args


class SimpleArgs(Args):
    """Simple argument set."""
    name: str
    ("--name",)
    count: int = 1
    ("--count",)


class ComplexArgs(Args):
    """Complex argument set with many fields."""
    name: str
    ("--name",)
    version: str = "1.0.0"
    ("--version",)
    output: str = "output.txt"
    ("--output",)
    verbose: bool = False
    ("--verbose",)
    dry_run: bool = False
    ("--dry-run",)
    config: ty.Optional[str] = None
    ("--config",)
    workers: int = 4
    ("--workers",)


def bench_parser_build():
    """Benchmark parser construction time."""
    def build_simple():
        SimpleArgs.build_parser()

    def build_complex():
        ComplexArgs.build_parser()

    simple_time = timeit.timeit(build_simple, number=1000)
    complex_time = timeit.timeit(build_complex, number=1000)

    print(f"Parser build (simple):  {simple_time/1000*1000:.3f} ms per call")
    print(f"Parser build (complex): {complex_time/1000*1000:.3f} ms per call")


def bench_parsing():
    """Benchmark argument parsing time."""
    simple_parser = SimpleArgs.build_parser()
    complex_parser = ComplexArgs.build_parser()

    def parse_simple():
        simple_parser.parse_args(["--name", "test", "--count", "5"])

    def parse_complex():
        complex_parser.parse_args([
            "--name", "app",
            "--version", "2.0.0",
            "--output", "out.txt",
            "--verbose",
            "--config", "app.yml",
            "--workers", "8"
        ])

    simple_time = timeit.timeit(parse_simple, number=10000)
    complex_time = timeit.timeit(parse_complex, number=10000)

    print(f"Parse args (simple):  {simple_time/10000*1000:.3f} ms per call")
    print(f"Parse args (complex): {complex_time/10000*1000:.3f} ms per call")


if __name__ == "__main__":
    print("=== Duho Benchmark Results ===\n")
    bench_parser_build()
    print()
    bench_parsing()
