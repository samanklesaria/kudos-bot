For python code, leave only one line between function definitions.
Use lisp style ending parens. For example, do this:
```python
call(
    arg1=1,
    arg2=2)
```

NOT this:

```python
call(
    arg1=1,
    arg2=2
)
```

Be terse. When giving examples, provide only one example per piece of functionality. Prefer a functional style to object-oriented programming. Do not create new files or directories unless absolutely necessary. Do not over-generalize: do exactly what the user asks for, without any additional features or functionality. Write the simplest code possible: avoid creating new abstractions. 

Write tests using pytest, with parameterization where appropriate. Only functions, no classes. You can also use the `hypothesis` library if you need to generate random data for your tests. Express iteration in terms of list comprehensions whenever possible. Don't worry about trying to satisfy the type checker. Never turn a generator into a list unless you absolutely need to: use iterators instead of lists whenever possible.

When writing SQL, use a declarative style. Avoid PL/SQL and DECLARE statements whenever you can, using CTE and views instead. 

After making changes to the schema, migrate the database:

```sh
sh scripts/migrate.sh
```
