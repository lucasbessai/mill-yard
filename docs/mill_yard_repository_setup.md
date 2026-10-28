# Setting Up the `mill-yard` Python Repository

This document explains the repository structure and Python tooling selected for `mill-yard`, a Python project for developing a discrete-event simulation of forestry mill-yard operations and methods for evaluating or optimizing operating policies.

The intended reader may be new to Python packaging and collaborative software development. This documentation explains not only what each file does, but why it belongs in the repository. The project scope and model design are still evolving; the structure below is a starting point that can change as requirements become clearer.

## 1. Repository layout

```text
mill-yard/
├── README.md
├── .gitignore
├── .pre-commit-config.yaml
├── pyproject.toml
├── uv.lock
├── .python-version
├── configs/
├── docs/
├── scripts/
├── src/
│   └── mill_yard/
└── tests/
```

Each location has a distinct role:

| Location | Purpose |
|---|---|
| `README.md` | Introduces the project and explains how to install, run, and develop it. |
| `.gitignore` | Keeps generated or machine-specific files, such as `.venv/`, out of Git. |
| `.pre-commit-config.yaml` | Defines checks that run on staged files when a developer commits. |
| `pyproject.toml` | Declares package metadata, dependencies, build instructions, and tool settings. |
| `uv.lock` | Records the resolved dependency versions used by `uv` for reproducible environments. Commit this file for this application/research repository. |
| `.python-version` | Tells tools such as `uv` which Python version to select for local development. |
| `configs/` | Stores inputs for particular simulation scenarios or experiments, such as loader capacities or demand schedules. |
| `docs/` | Holds design notes, diagrams, and project documentation. |
| `scripts/` | Holds standalone helper programs for development or analysis. Reusable project functionality belongs in the package under `src/`. |
| `src/mill_yard/` | Contains the importable Python package and the project’s implementation. |
| `tests/` | Contains automated checks for the package. |

The `.venv/` directory is created on a developer’s machine by `uv sync`; it should be ignored by Git, not committed. It contains the local Python environment and installed packages, which can be recreated from the project files.

## 2. `pyproject.toml`: package and tool configuration

`pyproject.toml` is the standard home for Python project metadata and packaging configuration. It can also hold settings for tools such as pytest and Ruff. Its sections use TOML (Tom’s Obvious Minimal Language), a text format designed to be readable and straightforward to edit. The PyPA documentation describes `pyproject.toml` as a configuration file used by packaging tools and other development tools [2].

A small example for this project is:

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "mill-yard"
version = "0.1.0"
description = "Simulation and decision support for forestry mill-yard operations"
readme = "README.md"
requires-python = ">=3.12"
dependencies = []

[project.optional-dependencies]
gui = []

[dependency-groups]
dev = []

[tool.hatch.build.targets.wheel]
packages = ["src/mill_yard"]
```

This is illustrative. The real dependency lists and optional features should reflect what the implementation actually needs.

### Build configuration

`[build-system]` tells packaging tools how to build the project into an installable distribution. In this example, Hatchling is the build backend. It is needed to build the package; it is not automatically a runtime dependency of the simulation [2].

Hatchling is one of several established backends. Setuptools, Flit, and PDM are other options. A backend might be changed if the project develops packaging requirements the current backend does not support conveniently. For example, a project may need a specialized build step, unusual source layout, or additional artifacts in its distribution. For a conventional pure-Python package, Hatchling is a reasonable choice [2, 4].

The `[tool.hatch.build.targets.wheel]` setting tells Hatchling where the package code lives. Here, the package is in `src/mill_yard/`, not at the repository root [4].

### Project metadata

`[project]` describes the installable project:

- `name` is the distribution name used by installers. It can use a hyphen (`mill-yard`).
- The Python import name uses an underscore (`mill_yard`): for example, `import mill_yard`.
- `version` identifies a release. `0.1.0` is a conventional starting version while the package is early in development.
- `description` and `readme` provide a short summary and the main project documentation.
- `requires-python` declares the Python versions the project supports [2].
- `dependencies` lists packages required by a default installation.

The declared minimum Python version is a compatibility promise: the code should work on that version. It is separate from `.python-version`, which selects a version for local development. For example, `requires-python = ">=3.12"` says Python 3.12 is the minimum supported version; `.python-version` containing `3.12` asks local tools to use Python 3.12. An upper bound should only be added when there is a concrete compatibility reason to exclude later versions.

As the package evolves, reassess `requires-python` if it starts using syntax or standard-library features unavailable in the declared minimum, or if dependencies stop supporting that version.

### Runtime, optional, and development dependencies

Dependencies belong in different sections depending on who needs them and when:

- **Default runtime requirements** go in `[project].dependencies`. These are installed for users of the package by default.
- **Optional installable features** go in `[project.optional-dependencies]`. An extra such as `gui` can keep GUI-specific packages out of a headless simulation or optimization installation. This is useful only if the core project can still be used without that feature [2].
- **Development tools** go in `[dependency-groups].dev`. Testing, linting, formatting, and commit tools help contributors work on the repository; they generally should not become requirements for someone who only installs the application [3].

The boundary is based on role, not on a package’s name. For example, SimPy belongs in runtime dependencies if the simulation engine imports it to run. A plotting package could be a runtime dependency if plotting is part of the core package, or an optional extra if only a separate reporting feature uses it. A GUI library can be an optional extra when users can run simulations without opening the GUI.

`uv add simpy` adds a runtime dependency. `uv add --dev pytest ruff` adds development dependencies. `uv` updates `pyproject.toml` and the lockfile as appropriate [6, 7].

An installation can request extras, for example:

```powershell
uv sync --extra gui
```

For a repository checkout, `uv sync` installs the default development dependency group unless configured otherwise [7]. Extras must be requested explicitly; `uv sync --all-extras` requests all project extras. For a published package, users can request extras with a command such as `pip install "mill-yard[gui]"`. Development dependency groups are for repository workflows and are not published as installable extras [3].

## 3. Development tools

### pytest and pytest-xdist

`pytest` discovers tests based on conventional filenames and function names, runs them, and reports failures [9]. For this project, tests can express engineering invariants, for example:

- material volume is conserved when a loader transfers logs between locations;
- inventory never becomes negative;
- a loader cannot begin a task while busy;
- arrivals and demand events occur at the configured times;
- the same deterministic inputs and seed produce the same results.

`pytest-xdist` is an add-on that can run tests in multiple worker processes. A setting such as `-n auto` starts workers based on the available CPUs; `-n 0` disables parallel execution [10]. Parallelism can help once the test suite is large enough, but starting workers has overhead. Tests also need to be independent—for example, they should not write to the same output file. It is reasonable to keep xdist if the project expects many or slow simulation tests; otherwise it can be added later.

### Ruff

Ruff is a single tool that provides both a linter and a formatter [11].

- The **linter** checks for likely mistakes and consistency issues, such as unused imports, suspicious constructs, or imports that should be sorted.
- The **formatter** rewrites code into a consistent layout, such as line breaks and indentation.

You do not install a separate package for Ruff’s formatter. A single `ruff` dependency provides both `ruff check` and `ruff format` commands. Its configuration can live in `pyproject.toml` [11]. Start with a small set of clear rules and expand it when the team has a reason to do so.

Ruff’s formatter aims to produce output close to Black’s and is intended as a compatible alternative for most projects [11, 12]. Using Ruff for formatting therefore usually avoids needing Black as a second formatter. If a project already has a large Black-formatted codebase, compare the outputs before switching; do not configure two formatters to rewrite the same files.

### pre-commit

`pre-commit` installs a Git hook that runs configured checks before a commit is created. The repository’s `.pre-commit-config.yaml` describes the hooks. A local hook with `repo: local` is defined in the project itself rather than downloaded from another hook repository. With `language: system`, the hook uses tools already available in the developer’s environment; in this project, the entries call Ruff through `uv run` [13].

Hooks usually run on staged files of the specified type [13]. If a hook changes a file, Git stops the commit so the developer can review and stage the resulting change, then commit again. The hook does not automatically run the full test suite unless a test hook is added.

### Tool settings

Tools can run with defaults, so their configuration sections are optional. Shared settings become useful when the team wants consistent conventions—for example, where tests live, what Python syntax is targeted, which lint rules are enabled, or what line length the formatter uses.

Examples include:

- `[tool.pytest.ini_options]` for test paths, markers, and default pytest options [9];
- `[tool.ruff]` and `[tool.ruff.lint]` for Ruff’s target Python version, source paths, and lint rules [11];
- `[tool.ruff.format]` for formatter choices such as quote style or line endings [11].

Avoid adding settings solely because another repository has them. Each setting is a project decision and should have a reason the team understands.

## 4. `uv`: environments and locked dependencies

`uv` manages Python project environments and dependencies. It provides one workflow for resolving dependencies, creating a virtual environment, installing the package, and recording resolved versions in `uv.lock` [6, 8]. The `uv` executable itself is installed separately; Astral documents standalone installers and package-manager options, including Windows installation methods [5].

The main files have different jobs:

- `pyproject.toml` describes the project and its dependency requirements, such as “NumPy version 1.26 or later.”
- `uv.lock` records the versions resolved for the current dependency graph, including platform-specific details where needed [8].
- `.venv/` contains the actual local environment and installed packages. It is generated and should not be committed.
- `.python-version` selects the Python version for local development [15].

A typical setup from the repository root is:

```powershell
uv python pin 3.12
uv sync
```

`uv python pin` writes the requested Python version to `.python-version` [15]. `uv sync` creates `.venv` if needed, resolves and installs the project’s dependencies, and creates or updates `uv.lock` [6, 8]. Add an extra when needed, for example `uv sync --extra gui`; use `uv sync --all-extras` when you want every extra [8]. Commit `.python-version` and `uv.lock` so collaborators share the intended Python selection and resolved dependencies. Keep `.venv/` ignored.

Use `uv run <command>` to run a project command in the managed environment [6], such as:

```powershell
uv run pytest
uv run ruff check .
uv run ruff format --check .
```

## 5. Configuration files: project versus experiment

There are two different kinds of configuration in this repository:

1. **Software/tool configuration** belongs mainly in `pyproject.toml` and `.pre-commit-config.yaml`. It controls how the package is built, tested, linted, formatted, and checked before commits.
2. **Simulation and experiment configuration** belongs in files under `configs/`. It describes a particular modeled yard or run: for example, whether an entrance scaler is present, loader capacities, supply and demand schedules, or random seeds.

YAML is a reasonable format for experiment configurations because it is readable and supports nested settings. It is not mandatory; TOML or JSON could also work. The choice should depend on readability, validation needs, and how configurations will be edited. Whatever format is chosen, validate configuration values when loading them so invalid capacities or unknown options fail with a clear message rather than causing confusing errors during a run.

Keep configuration data separate from the code that interprets it. For example, a YAML file may select an operating scenario or policy, while Python classes and functions define what that selection means. This makes it easier to compare scenarios without editing source code.

## 6. Source package structure

The `src/` layout separates importable package code from repository-level files. It helps avoid accidentally importing code from the working tree instead of the installed package, which can hide packaging problems [1]. The `src/` directory itself is just a source-code container; `src/mill_yard/` is the importable package and gives the project its own namespace, such as `mill_yard.engine` [1, 14].

A possible starting structure is:

```text
src/
└── mill_yard/
    ├── __init__.py
    ├── cli.py
    ├── config/
    ├── model/
    ├── engine/
    ├── scenarios/
    ├── policies/
    ├── optimization/
    ├── records/
    ├── evaluation/
    └── gui/
```

This is a design sketch, not a requirement to create every directory immediately. Add modules when there is functionality to put in them. One possible interpretation is:

- `model/`: domain concepts and state, such as trucks, log piles, loaders, and the infeed;
- `engine/`: simulation time, event scheduling, and event execution;
- `scenarios/`: deterministic or stochastic supply and demand inputs;
- `policies/`: rules that select feasible operating decisions, including a human-controlled policy;
- `optimization/`: methods that search over policies or other design choices;
- `records/`: run logs and data needed to inspect or replay a simulation;
- `evaluation/`: metrics and comparisons across runs;
- `gui/`: visualization, manual operation, and replay interfaces;
- `config/`: loading, validating, and translating user configuration files into typed settings.

The exact boundaries should follow the model as it develops. In particular, the simulation engine should expose a stable interface for advancing the model and observing events. That gives the command-line interface, GUI, tests, and optimization code a common way to run or inspect simulations.

### `__init__.py`

A minimal `__init__.py` can contain only a package docstring:

```python
"""Simulation and decision-support tools for forestry mill-yard operations."""
```

It makes the package intent explicit and provides a place to expose a small set of stable top-level names later, if useful. Python’s documentation explains that `__init__.py` marks a regular package and may be empty or contain initialization code [14]. Avoid importing all subsystems from `__init__.py`; doing so can create circular imports or make importing the package unnecessarily slow. Modern Python also supports namespace packages without `__init__.py`, but an explicit file is clear and conventional for this package [14].

### `__init__.py` files in subdirectories

The same idea applies to directories nested inside `mill_yard/`. If a directory is intended to be an importable part of the package, it can have its own `__init__.py`. For example:

```text
src/mill_yard/
├── __init__.py
├── engine/
│   ├── __init__.py
│   ├── event_calendar.py
│   └── simulation.py
└── config/
    ├── __init__.py
    └── loader.py
```

These files make `mill_yard.engine` and `mill_yard.config` regular Python subpackages [14]. Code elsewhere in the project can then import modules using their full package paths:

```python
from mill_yard.engine.simulation import Simulation
from mill_yard.config.loader import load_config
```

A subpackage’s `__init__.py` can be empty or contain a short docstring. It does not need to re-export everything in that directory. Later, if there is a small, stable public interface, it may re-export selected names—for example, `from .simulation import Simulation`—so callers can write `from mill_yard.engine import Simulation`. Keeping these exports limited helps avoid circular imports and makes it clearer which parts are intended for other modules to use.

Python can also treat directories without `__init__.py` as namespace packages. For this project, use `__init__.py` in source directories that are intended to be regular importable subpackages; do not add one mechanically to every directory. In particular, keep automated tests in the repository-level `tests/` directory rather than creating `src/mill_yard/test/`. Tests are development checks, not part of the importable application package. A `tests/__init__.py` is usually unnecessary with pytest unless tests specifically need to import one another as a package.

### `cli.py`

The command-line interface is an adapter between terminal commands and package functionality. If `pyproject.toml` contains:

```toml
[project.scripts]
mill-yard = "mill_yard.cli:main"
```

then installation creates a `mill-yard` command that calls `main()` in `src/mill_yard/cli.py` [2]. The CLI can eventually provide commands such as running a simulation, evaluating a policy, optimizing policy parameters, printing a resolved configuration, or opening a replay in the GUI.

Keep the CLI focused on parsing arguments and coordinating calls. The simulation rules belong in the engine/model; optimization algorithms belong in `optimization/`. This allows the GUI and command-line tools to use the same underlying functionality.

### Configuration code and experiment files

A `config/` package inside `src/mill_yard/` and a `configs/` directory at the repository root serve different purposes:

- `src/mill_yard/config/` contains Python code that loads, validates, and converts configuration data into settings used by the package.
- `configs/` contains example or study-specific input files.

A configuration file may select a policy or a simulation mode, but the implementation of tree search, simulation, or another optimization method should live in Python modules rather than being encoded as a collection of configuration fields.

## 7. Guiding principles

The structure should help the project stay understandable as it grows, without forcing every possible subsystem into existence on day one.

1. **Keep domain logic in the package.** Scripts, the CLI, and the GUI should call shared engine and policy interfaces instead of duplicating simulation rules.
2. **Keep experiment inputs separate from software settings.** Yard layouts and supply-demand scenarios are not packaging configuration.
3. **Make assumptions explicit and testable.** For a simulation, tests should check conservation, feasibility, event ordering, and repeatability where applicable.
4. **Record reproducibility information.** Use configuration files and random seeds to identify a run; save enough event or state information to inspect and replay it.
5. **Grow the structure with the model.** The directory sketch is a starting point. Add abstractions when they clarify real responsibilities, and revise them when evidence shows a better separation.

## 8. How the setup reflects software engineering principles

The repository structure is more than a set of folders. It puts several broad software engineering ideas into practice. These ideas help a team understand how parts of a system relate, how to make changes safely, and how to reproduce results.

### Separation of concerns

Each part of the project should have a clear responsibility. The engine advances the simulated yard, policies choose actions, optimization methods search for better policies or design choices, and the GUI displays or accepts decisions. The CLI and GUI are different ways to interact with the same underlying model.

This separation matters when requirements change. If a new optimization method is added, it should be able to use the engine without requiring the GUI to be rewritten. If the GUI changes, the rules for truck arrivals or loader movement should not have to change with it. Keeping responsibilities separate limits how far a change needs to spread.

### Modularity and interfaces

Modules are useful when they create clear boundaries, not simply because a project has many folders. A module should provide a coherent capability and communicate with other modules through understandable functions, classes, or data structures.

For example, the engine might provide a way to advance the simulation to the next event and inspect the resulting state. The CLI, GUI, policy evaluator, and tests could all use that interface. If the engine later changes internally—for example, from a custom event calendar to a DES library—the other parts may need fewer changes if that public interface remains stable.

This is a form of **encapsulation**: other parts of the package depend on what a component promises to do, rather than on all of its internal details.

### Configuration separates scenarios from implementation

Representing yard layouts, equipment, and supply-demand conditions as configuration data separates the questions “What scenario should be run?” and “How does the simulation work?” A run might specify that an entrance scaler is present or select loaders with different capacities; the model interprets those choices.

This makes it easier to compare scenarios consistently and reduces the need for special-case edits to simulation code. Configuration should describe supported choices, while Python code should define the behavior of those choices. Validation and clear errors help catch invalid configurations before a long run begins.

### Tests make change safer

As the engine grows, a change in one area can unintentionally affect another. Tests provide repeatable checks of important properties. For example, tests can verify that material is conserved during transfers, loaders cannot perform conflicting tasks, and deterministic scenarios produce repeatable results for a fixed seed.

Tests do not prove that the model represents a real mill yard perfectly. They help establish that the implementation follows its specified rules. Validation against operational data and review of modeling assumptions are separate, necessary activities.

### Reproducibility and traceability

The lockfile, configuration files, random seeds, and saved run records support repeatable experiments. Together, they help answer: Which code and dependencies were used? Which yard scenario and policy were selected? Which random conditions were sampled? What happened during the run?

This is particularly important for research. A result is more useful when another person can identify the settings that produced it and repeat or inspect the run. A lockfile helps reproduce the software environment; it does not by itself guarantee identical results across every operating system, hardware setup, or source-code version.

### Automated checks reduce routine errors

Ruff, pytest, and pre-commit automate checks that developers might otherwise perform inconsistently. Formatting and lint checks improve consistency and catch common code problems; tests check selected behavior. These checks do not replace design review or engineering judgment, but they give the team fast, repeatable feedback.

### Preparing for growth without overengineering

In this project, “ready to scale” means that the code can accommodate likely new requirements without major rewrites. Likely changes include adding equipment types, switching between deterministic and stochastic supply or demand, introducing new policies, comparing many scenarios, and supporting both GUI and command-line workflows.

The package layout prepares for that growth by giving these responsibilities natural homes and encouraging shared interfaces. For example, a human-controlled policy and an optimized policy should be able to make decisions through the same policy interface, while the engine remains responsible for applying those decisions and advancing the yard state.

This is **structural readiness**, not a guarantee of computational speed. Running thousands of scenarios efficiently may later require profiling, parallel execution, or algorithmic improvements. Those choices should be based on measured workloads.

The goal is not to implement every possible subsystem now. A sound starting point supports the next real use case, keeps likely areas of change reasonably independent, and adds abstractions when they solve an observed problem. This avoids both a tangled codebase and an unnecessarily elaborate one.

## 9. References

1. Python Packaging Authority, [“src layout vs. flat layout”](https://packaging.python.org/en/latest/discussions/src-layout-vs-flat-layout/), Python Packaging User Guide.
2. Python Packaging Authority, [“Writing your `pyproject.toml`”](https://packaging.python.org/en/latest/guides/writing-pyproject-toml/), Python Packaging User Guide.
3. Python Packaging Authority, [PEP 735: Dependency Groups](https://peps.python.org/pep-0735/).
4. Hatch, [Build configuration](https://hatch.pypa.io/latest/config/build/).
5. Astral, [Installing uv](https://docs.astral.sh/uv/getting-started/installation/).
6. Astral, [Working on projects](https://docs.astral.sh/uv/guides/projects/).
7. Astral, [Managing dependencies](https://docs.astral.sh/uv/concepts/projects/dependencies/).
8. Astral, [Locking and syncing](https://docs.astral.sh/uv/concepts/projects/sync/).
9. pytest maintainers, [Configuration](https://docs.pytest.org/en/stable/reference/customize.html) and [Good Integration Practices](https://docs.pytest.org/en/stable/explanation/goodpractices.html).
10. pytest-xdist maintainers, [pytest-xdist documentation](https://pytest-xdist.readthedocs.io/en/latest/).
11. Astral, [Ruff documentation](https://docs.astral.sh/ruff/), including its [linter](https://docs.astral.sh/ruff/linter/), [formatter](https://docs.astral.sh/ruff/formatter/), and [configuration](https://docs.astral.sh/ruff/configuration/) documentation.
12. Black maintainers, [The Black code style](https://black.readthedocs.io/en/stable/the_black_code_style/current_style.html).
13. pre-commit maintainers, [pre-commit documentation](https://pre-commit.com/).
14. Python Software Foundation, [“Modules and Packages”](https://docs.python.org/3/tutorial/modules.html#packages), The Python Tutorial.
15. Astral, [Python versions](https://docs.astral.sh/uv/concepts/python-versions/).
