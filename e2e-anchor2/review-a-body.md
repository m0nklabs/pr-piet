## PR-Piet review — changes requested
<!-- pr-agent:review:full -->

head `1a9fdc7f` · 1 blocking

### Findings

1. **Off-by-one Pagination** — `chunk_utils.py:17`
   The loop step is `size + 1` instead of `size`, so one item is skipped at every page boundary. For example, with 10 items and `size=3`, the starts are 0, 4, 8, producing chunks [0:3], [4:7], [8:11]; the items at index 3 and 7 are never included in any page. This silently drops data from the rendered report and also makes `total_pages` undercount. The step should be `size`.

<!-- pr-piet-review:v1 head=1a9fdc7f145de4b5e00cf8865eb66c9ca5ddc006 -->
