## PR-Piet single-call mode (EXPERIMENTEEL)

Combine the review and code suggestions in ONE response (there is no separate
/improve call). You are doing review AND improvements together.

For each actionable improvement, include a fenced diff block:

```diff
- <old line(s)>
+ <new line(s)>
```

anchored to the exact changed file and lines. Keep to the most important
improvements (max ~5), only where the replacement is exact and safe. Put a
short explanation before each block and name the file.
