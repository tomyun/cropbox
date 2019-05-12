from .trace import Trace
from .track import Track, Accumulate, Difference, Flip, Preserve
from .var import var

#HACK: to implement @optimize, need better way of controlling this
#TODO: probably need a full dependency graph between variables to push updates downwards
#FORCE_UPDATE = False

import random

class system(var):
    def init(self, obj, **kwargs):
        d = self.data(obj)
        try:
            s = kwargs[self.__name__]
        except KeyError:
            s = self.compute(obj)
        d[self] = s

    def compute(self, obj):
        cls = self._wrapped_fun
        #HACK: when System(s) to be returned were wrapped in __call__()
        if not isinstance(cls, type):
            cls = cls(obj)
        if cls is None:
            s = None
        elif isinstance(cls, list):
            s = []
        elif isinstance(cls, type):
            s = cls(context=obj.context, **{k: obj[v] for k, v in self._kwargs.items()})
        else:
            s = cls
        return s

class systemproxy(system):
    pass

class statevar(var):
    trace = Trace()

    def __init__(self, f=None, *, track, time='context.time', init=0, unit=None, alias=None):
        self._track_cls = track
        self._time_var = time
        self._init_var = init
        super().__init__(f, unit=unit, alias=alias)

    def time(self, obj):
        return obj[self._time_var]

    def init(self, obj, **kwargs):
        d = self.data(obj)
        t = self.time(obj)
        try:
            v = kwargs[self.__name__]
        except KeyError:
            v = obj[self._init_var]
        d[self] = self._track_cls(t, self.unit(obj, v))

    def get(self, obj):
        with self.trace(self, obj):
            #HACK: prevent recursion loop already in computation tree
            tr = super().get(obj)
            if self.trace.is_stacked(self):
                #FIXME: general approach
                if type(self) in {accumulate, difference, optimize}:
                    print(f'{self} stacked -- return {tr._value}')
                    return tr._value
                else:
                    #TODO: implement own exception
                    raise RecursionError(f'{self} stacked -- {self.trace.stack}')
            # support custom timestamp (i.e. elongation age instead of calendar time)
            t = self.time(obj)
            # lazy evaluation preventing redundant computation
            r = lambda: self.compute(obj)
            #HACK: prevent premature initialization?
            #return tr.update(t, r, force=self.trace.is_update_forced)
            return tr.update(t, r, regime=self.trace.regime)

class derive(statevar):
    def __init__(self, f=None, **kwargs):
        super().__init__(f, track=Track, **kwargs)

class accumulate(statevar):
    def __init__(self, f=None, **kwargs):
        super().__init__(f, track=Accumulate, **kwargs)

class difference(statevar):
    def __init__(self, f=None, **kwargs):
        super().__init__(f, track=Difference, **kwargs)

class flip(statevar):
    def __init__(self, f=None, **kwargs):
        super().__init__(f, track=Flip, **kwargs)

class constant(statevar):
    def __init__(self, f=None, **kwargs):
        super().__init__(f, track=Preserve, **kwargs)

#TODO: use @proxy <: @var replacing @property, also make @state <: @var

class proxy(derive):
    def time(self, obj):
        # doesn't change at t=0 ensuring only one update
        return 0

class parameter(proxy):
    def compute(self, obj):
        # allow override by external option
        v = obj.option(self)
        if v is None:
            v = super().compute(obj)
        return v

class drive(derive):
    def compute(self, obj):
        d = super().compute(obj) # i.e. return df.loc[t]
        return d[self.__name__]

class flag(derive):
    def __init__(self, f=None, prob=1, **kwargs):
        self._prob_var = prob
        super().__init__(f, unit=None, **kwargs)

    def check(self, obj):
        v = obj[self._prob_var]
        return True if v >= 1 else random.random() <= v

    def compute(self, obj):
        return self.check(obj) and super().compute(obj)

class produce(derive):
    def compute(self, obj):
        v = super().compute(obj)
        if v is None:
            return ()
        elif isinstance(v, tuple):
            systemcls, kwargs = v
        else:
            systemcls, kwargs = v, {}
        def f():
            s = systemcls(context=obj.context, parent=obj, children=[], **kwargs)
            obj.children.append(s)
        obj.context.queue(f)
        return v

import scipy.optimize

class optimize(derive):
    def __init__(self, f=None, *, lower=None, upper=None, **kwargs):
        self._lower_var = lower
        self._upper_var = upper
        super().__init__(f, **kwargs)

    def compute(self, obj):
        tr = self.data(obj)[self]
        i = 0
        def cost(x):
            nonlocal i
            regime = f'optimize-{obj.__class__.__name__}-{self.__name__}-{i}'
            i += 1
            print(f'@optimize: {x} ({regime})')
            with self.trace(self, obj, regime=regime):
                tr._value = x
                return super(optimize, self).compute(obj)
        l = obj[self._lower_var]
        u = obj[self._upper_var]
        #FIXME: minimize_scalar(method='brent/bounded') doesn't work with (l, r) bracket/bounds
        if None in (l, u):
            v = float(scipy.optimize.minimize_scalar(cost).x)
        else:
            v = scipy.optimize.brentq(cost, l, u)
        #FIXME: no longer need with regime?
        # trigger update with final value
        cost(v)
        return v
