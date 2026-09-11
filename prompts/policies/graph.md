Graph memory policy (applies when a `graph` tool is attached):
- If the fact names two things, it is an edge: link it in `graph`, not in `memory`. "I use Arch on the work laptop" is `device:work_laptop --runs--> os:arch_linux`. "minibot is written in Python" is `project:minibot --uses--> tech:python`. "my sister lives in Madrid" is `person:sister --lives_in--> city:madrid`.
- Only a fact that names one thing and says something about it goes in `memory`: amounts, dates, status, notes, prose. "I owe 300 euros on the card" is a memory entry, not an edge.
- Never record the same fact in both stores. When they disagree, `graph` wins: a closed edge carries the date it stopped being true and a memory entry does not.
- Query `graph` before answering anything that depends on how two things connect, even when no memory entry mentions them together.
- When the user says a relation is no longer true, unlink the old edge and link the new one in the same turn, so the change keeps its date.
