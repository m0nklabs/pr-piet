## PR-Piet review — changes requested
<!-- pr-agent:review:incremental -->

head `ccf03598` · 1 blocking

### Findings

1. **Off-by-one** — `chunk_utils.py:17`
   The pagination loop advances by `size + 1` while each page only consumes `size` items, so one item is dropped at every page boundary. With 10 items and `size=3`, the loop starts at 0, 4 and 8, producing pages `[0:3]`, `[4:7]`, `[8:11]`; the items at index 3 and 7 are never rendered (and `total_pages`/`flatten` inherit the loss). The step should equal the page size.

<!-- pr-piet-review:v1 head=ccf03598f274a21a6f5b8027ede9125d7df69bbe -->
