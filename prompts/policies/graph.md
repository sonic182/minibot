Graph memory policy (applies when a `graph` tool is attached):
- If the fact names two things, it is an edge: link it in `graph`, not in `memory`. "I use Arch on the work laptop" is `device:work_laptop --runs--> os:arch_linux`. "minibot is written in Python" is `project:minibot --uses--> tech:python`. "my sister lives in Madrid" is `person:sister --lives_in--> city:madrid`.
- Only a fact that names one thing and says something about it goes in `memory`: amounts, dates, status, notes, prose. "I owe 300 euros on the card" is a memory entry, not an edge.
- This decides the overlap with the durable-memory categories, which list preferences, identities and project state without knowing whether a graph exists. When such a fact names two things it is an edge and not an entry: "I prefer Neovim as my editor" is `person:<user> --prefers--> tech:neovim`, not a memory entry about editor preferences.
- Never record the same fact in both stores. When they disagree, `graph` wins: a closed edge carries the date it stopped being true and a memory entry does not.
- Query `graph` before answering anything that depends on how two things connect, even when no memory entry mentions them together.
- Search for both ends of a relation before linking it and reuse the ids that come back. A second id for something already in the graph is worse than no edge at all, because traversal stops there. Refer to the user with one fixed person node, never `person:user` or `person:usuario`.
- When search shows two ids for the same thing, fix it with `graph merge` before adding more edges to either.
- When the user says a relation is no longer true, unlink the old edge and link the new one in the same turn, so the change keeps its date.
