## Candidate completeness

Implement one agreed behavior per PR. Aim for 3-6 files; more than 15 hand-maintained changed files
requires David's explicit approval recorded on the ticket. Count tests, docs and configuration too;
report generated changes separately. Preserve explicit existing scope decisions such as FISK-172.
For event/money work, the first candidate includes the plan's fact list, invariant assertions and
every-order permutation regression test, plus applicable replay and overlapping-writer cases.
Run every required MySQL suite and report complete results and missing prerequisites honestly.
Do not silently sample permutations or substitute an in-memory fake for required MySQL evidence.
Complete the safe observation slice before implementing behavior dependent on unobserved external
facts, unless the owner explicitly authorized that assumption. Freeze the candidate during the
three-review round. Start review fixes only after its combined findings packet arrives. Rework all
blocking findings together, preserve follow-ups, and provide a new SHA and resolution map.
