def singleton(class_):
    class Wrapper(class_):
        _instance = None

        def __new__(cls, *_args, **_kwargs):
            if Wrapper._instance is None:
                Wrapper._instance = super().__new__(cls)
                Wrapper._instance._sealed = False
            return Wrapper._instance

        def __init__(self, *args, **kwargs):
            if self._sealed:
                return
            super().__init__(*args, **kwargs)
            self._sealed = True

    Wrapper.__name__ = class_.__name__
    return Wrapper
