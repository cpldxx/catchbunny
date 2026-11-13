"""The demo shop, under the path the benchmark, the tests and older notes use.

It lives in the package (catchbunny.demo_shop) so that `catchbunny demo` can start
it from an installed copy. This file only keeps `python bench/buggy_shop.py` working.
"""

from catchbunny.demo_shop import BUGS, PASSWORD, PRODUCTS, USER, create_shop, main, parse_bugs

__all__ = ["BUGS", "PASSWORD", "PRODUCTS", "USER", "create_shop", "parse_bugs"]

if __name__ == "__main__":
    main()
