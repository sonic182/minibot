# Review Checklist

## Architecture

- Is the file in the correct package for the responsibility it owns?
- Does it import inward, or is it reaching across layers?
- Is it mixing orchestration, transport, persistence, and provider logic in one place?
- Are framework or provider-native types leaking into `core` or deeper into `app` than necessary?
- Is `shared` being used for a genuinely generic helper, or as a shortcut around boundaries?
- Does a bundled extension remain a thin `register(mb)` composition module while its concrete transport/persistence code stays in `adapters`?
- Does the extension use `ExtensionContext` rather than `AppContainer` or private container state?
- Do extension tools enter through `mb.tool`/`mb.add_tool` so normal tool validation, event emission, and output handling still apply?

## Async

- Is async used across I/O paths?
- Is blocking work hidden inside an async function?
- Are retry, timeout, and cancellation handled in one coherent layer?
- Are background tasks explicit, bounded, and tied to runtime lifecycle?
- Are contributed services owned by `ExtensionRegistry`, rather than started from `register()`?
- Does a channel extension reject non-daemon entrypoints before constructing a bus-subscribing service?
- Are event handlers short, lossy-safe observers that tolerate dropped events and cannot fail a live turn?

## Behavioral safety

- Does the refactor preserve current request and response flow?
- Are tool visibility, delegation, or handler semantics changing unintentionally?
- Are storage and provider boundaries still explicit after the change?
- Does an extension fail fast at import/registration while runtime handler errors remain isolated and logged?
- If task-worker behavior is claimed, does the worker explicitly load extensions? It does not today.

## Refactoring

- Can the problem be solved by moving logic to the owning layer instead of adding abstraction?
- Would a protocol or narrow contract reduce coupling?
- Is the refactor incremental, or is it creating a broad rewrite without clear benefit?
- Does the change improve testability without moving infra details inward?

## Style

- Are names explicit and aligned with the owning layer?
- Is the module focused?
- Is error handling placed near the relevant boundary?
- Are mapping and serialization kept near the edge that owns them?
