"""Task state machine package.

Do not import ``machine`` (or anything that imports ``forgeai.models``) here.
``from forgeai.state_machine.states import TaskState`` loads this file first;
pulling in ``TaskStateMachine`` would create a cycle: ``models.task`` →
``states`` → ``__init__`` → ``machine`` → ``models.task``.

Import explicitly::

    from forgeai.state_machine.states import TaskState
    from forgeai.state_machine.machine import TaskStateMachine
"""
