import copy
from os import listdir
from os.path import isfile, join
import importlib
import inspect
import builtins


class ObjectRegistry:
    """
    Registry for storing and retrieving objects (primarily classes) by name.

    Purpose
    -------
    - Maintain a name → object mapping for dynamic lookup.
    - Provide a unified registry that includes Python built-in types.
    - Support dynamic registration and discovery of classes at runtime.

    Design Notes
    ------------
    - Objects are keyed by their `__name__`.
    - Built-in types are pre-registered for convenience.
    - Duplicate registration is explicitly forbidden.
    """

    # Pre-populate registry with all Python built-in types
    built_in_types = {
        name: getattr(builtins, name)
        for name in dir(builtins)
        if isinstance(getattr(builtins, name), type)
    }

    def __init__(self):
        """
        Initialize the registry with built-in types.

        Notes
        -----
        - Uses a deep copy to ensure registry isolation.
        """
        self.objects: dict[str, object] = copy.deepcopy(self.built_in_types)

    def __contains__(self, item: str) -> bool:
        """
        Support `in` operator to test for object registration.

        Example
        -------
        if "int" in registry:
            ...
        """
        return item in self.objects

    def register(self, obj: type):
        """
        Register a new object in the registry.

        Parameters
        ----------
        obj : type
            Class or type object to register.

        Raises
        ------
        ValueError
            If an object with the same name is already registered.
        """
        if obj.__name__ in self.objects:
            raise ValueError("Objects can't be registered twice.")

        self.objects[obj.__name__] = obj

    def get(self, obj_name: str):
        """
        Retrieve a registered object by name.

        Parameters
        ----------
        obj_name : str
            Name of the object to retrieve.

        Returns
        -------
        object
            The registered object.

        Raises
        ------
        ValueError
            If the object name is not found.
        """
        if obj_name in self.objects:
            return self.objects[obj_name]

        raise ValueError(f"'{obj_name}' not found.")

    def register_all_class(self, path: str):
        """
        Automatically discover and register all classes in modules at a path.

        Purpose
        -------
        - Enable plugin-style registration.
        - Automatically expose all class definitions found in a directory.

        Parameters
        ----------
        path : str
            Filesystem path containing Python modules.

        Notes
        -----
        - Assumes files in the directory are importable modules.
        - Imports each module and registers all detected classes.
        """
        files = [
            f for f in listdir(path)
            if isfile(join(path, f))
        ]

        for x in files:
            module = importlib.import_module(x)

            # Register all classes defined in the module
            for name, obj in inspect.getmembers(module, inspect.isclass):
                self.register(obj)