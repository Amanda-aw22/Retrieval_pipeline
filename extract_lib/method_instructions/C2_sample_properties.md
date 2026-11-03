# INPUT (method C, step 2 — one target sample)

You are given ONE target sample from a research paper, the list of the other
samples in the same paper, and the numbered, labeled passages that mention the
target (plus the abstract and the "Herein / In this work" passage for
context). Passage numbers are the paper's global numbers, so they may skip.

Extract property records for the TARGET SAMPLE ONLY:

- Every record's `sample` must be exactly the target's canonical name, even
  when the passage uses one of its aliases.
- A value that belongs to any of the OTHER SAMPLES is not extracted here, even
  if it appears in the same sentence or table row group as the target.
  Read tables carefully: take only the row / column that belongs to the target.
- If the passages report nothing for the target, return an empty `records` list.
- Put the passage number of each quote in `passage`.
