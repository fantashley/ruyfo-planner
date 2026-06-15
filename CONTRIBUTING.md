# Contributing

## Commit messages: Conventional Commits

This repo uses [Conventional Commits](https://www.conventionalcommits.org/) so that
releases and the changelog can be generated automatically by
[release-please](https://github.com/googleapis/release-please).

Each commit message on `main` must start with a type:

```
<type>[optional scope]: <description>

[optional body]

[optional footer(s)]
```

Common types:

| Type       | Use for                                          | Version bump |
| ---------- | ------------------------------------------------ | ------------ |
| `feat`     | a new feature                                    | minor        |
| `fix`      | a bug fix                                         | patch        |
| `docs`     | documentation only                               | none         |
| `refactor` | code change that neither fixes a bug nor adds a feature | none  |
| `perf`     | performance improvement                          | patch        |
| `test`     | adding or fixing tests                           | none         |
| `build`    | build system or dependency changes               | none         |
| `ci`       | CI configuration                                 | none         |
| `chore`    | other changes that don't touch src or tests      | none         |

Examples:

```
feat(solver): position cars at the dropper's home the evening of the ride
fix: gate day-of car positioning by stated willingness
docs: explain the magic-link recovery flow
```

### Breaking changes

Add a `!` after the type or a `BREAKING CHANGE:` footer to trigger a major
version bump:

```
feat!: drop support for Python 3.10
```

## How releases work

1. Merge Conventional Commits into `main`.
2. The `release-please` workflow opens (and keeps updating) a "release PR" that
   bumps the version in `pyproject.toml` and `app/__init__.py`, and updates
   `CHANGELOG.md`.
3. Merging that release PR tags the release (e.g. `v0.2.0`) and publishes a
   GitHub Release.
