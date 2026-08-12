Only use `try` and `except` when you are truly implementing recovery-from-failure logic.
Prefer `pathlib` over `os` operations for dealing with files/paths.
Prefer `logger` calls over `print`.

Add commands to the `commands` dir. To define a command, name the python file after the command, e.g. `my_function.py`, and declare it with e.g.:

```python
def my_function(argument1: str) -> None:
    ...logic here...


@click.command(name="my-function")
@click.argument("argument1")
def my_function_cmd(argument1: str):
    """Documentation goes here
    """
    my_function(argument1)
```

Then register it in `cli.py` with e.g.:

```python
main.add_command(common_command_wrapper(my_function_cmd))
```

`boilersync` is a template CLI that can generate projects from templates and keep source templates updated as derivative projects evolve.
