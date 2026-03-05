# Review Checklist

## Architecture

- Is the file in the correct package for the responsibility it owns?
- Does it import inward, or is it reaching across layers?
- Is it mixing orchestration, transport, persistence, and provider logic in one place?
- Are framework or provider-native types leaking into `core` or deeper into `app` than necessary?
- Is `shared` being used for a genuinely generic helper, or as a shortcut around boundaries?

## Async

- Is async used across I/O paths?
- Is blocking work hidden inside an async function?
- Are retry, timeout, and cancellation handled in one coherent layer?
- Are background tasks explicit, bounded, and tied to runtime lifecycle?

## Behavioral safety

- Does the refactor preserve current request and response flow?
- Are tool visibility, delegation, or handler semantics changing unintentionally?
- Are storage and provider boundaries still explicit after the change?

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
