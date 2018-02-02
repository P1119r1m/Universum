#!/usr/bin/env python
# -*- coding: UTF-8 -*-
# pylint: disable = redefined-outer-name, invalid-name

import mock
import pytest

from _universum.gravity import construct_component, define_arguments_recursive, \
    Module, Dependency, get_name_to_module_map, get_dependencies
import _universum.module_arguments
import _universum.gravity


@pytest.fixture()
def mock_module():
    class MockedModule(object):
        def __init__(self):
            self.__dict__['_mock_children'] = {}

        def __getattr__(self, item):
            result = self._mock_children.get(item)

            if result is None:
                result = mock.MagicMock()
                self._mock_children[item] = result

            return result

    old_module = _universum.gravity.Module
    _universum.gravity.Module = MockedModule

    yield MockedModule

    _universum.gravity.Module = old_module


def mock_define_arguments(klass):
    patcher = mock.patch.object(klass, "define_arguments", create=True)
    patcher.start()
    return klass


class StaticallyDefinedModule1(Module):
    pass


class StaticallyDefinedModule2(Module):
    pass


def test_name_to_module_map_dynamic(mock_module):
    class First(mock_module):
        pass

    class Second(mock_module):
        pass

    assert get_name_to_module_map() == {'First': First, 'Second': Second}


def test_name_to_module_map_dynamic_another_context(mock_module):
    class Third(mock_module):
        pass

    class Fourth(mock_module):
        pass

    assert get_name_to_module_map() == {'Third': Third, 'Fourth': Fourth}


def test_name_to_module_map_static_context():
    module_map = get_name_to_module_map()
    assert module_map['StaticallyDefinedModule1'] == StaticallyDefinedModule1
    assert module_map['StaticallyDefinedModule2'] == StaticallyDefinedModule2


def test_name_to_module_map_inheritance(mock_module):
    class Root(mock_module):
        pass

    class Root2(mock_module):
        pass

    class Child(Root):
        pass

    class Derived1(Root):
        pass

    class Derived2(Root):
        pass

    class Bottom(Derived1, Derived2):
        pass

    assert get_name_to_module_map() == {'Root': Root, 'Root2': Root2, 'Child': Child,
                                        'Derived1': Derived1, 'Derived2': Derived2, 'Bottom': Bottom}


def test_dependencies_simple(mock_module):
    class RootOneUser(mock_module):
        dep = Dependency("OnlyDep")

    class OnlyDep(mock_module):
        pass

    assert get_dependencies(RootOneUser, get_name_to_module_map()) == [RootOneUser, OnlyDep]
    assert get_dependencies(OnlyDep, get_name_to_module_map()) == [OnlyDep]


def test_dependencies_two_users(mock_module):
    class RootTwoUsers(mock_module):
        first = Dependency("FirstDep")
        second = Dependency("SecondDep")

    class FirstDep(mock_module):
        dep = Dependency("Common")

    class SecondDep(mock_module):
        dep = Dependency("Common")

    class Common(mock_module):
        pass

    dependencies = get_dependencies(RootTwoUsers, get_name_to_module_map())
    for x in (RootTwoUsers, FirstDep, SecondDep, Common):
        assert x in dependencies


def test_define_arguments_mock_no_call(mock_module):
    class RootDefineArgs(mock_module):
        dep = Dependency("FirstDependency")

    @mock_define_arguments
    class FirstDependency(mock_module):
        pass

    @mock_define_arguments
    class NotADependency(mock_module):
        pass

    FirstDependency.define_arguments.assert_not_called()
    NotADependency.define_arguments.assert_not_called()

    parser = mock.MagicMock()
    define_arguments_recursive(RootDefineArgs, parser)

    assert parser.dest_prefix == ""
    FirstDependency.define_arguments.assert_called_with(parser)
    NotADependency.define_arguments.assert_not_called()


def test_define_arguments_3(mock_module):
    class ChainStart(mock_module):
        dep = Dependency("ChainSecond")

        @staticmethod
        def define_arguments(parser):
            parser.add_argument('--option1')

    class ChainSecond(mock_module):
        dep = Dependency("ChainEnd")

        @staticmethod
        def define_arguments(parser):
            parser.add_argument('--option2')

    class ChainEnd(mock_module):
        @staticmethod
        def define_arguments(parser):
            group_parser = parser.get_or_create_group("ChainEnd group")
            group_parser.add_argument('--option3')

    parser = _universum.module_arguments.ModuleArgumentParser()
    define_arguments_recursive(ChainStart, parser)
    settings = parser.parse_args(["--option2=abc"])
    assert settings.ChainStart.option1 is None
    assert settings.ChainSecond.option2 == "abc"
    assert settings.ChainEnd.option3 is None

    parser = _universum.module_arguments.ModuleArgumentParser()
    define_arguments_recursive(ChainSecond, parser)
    settings = parser.parse_args(["--option2=def"])
    assert 'ChainStart' not in dir(settings)
    assert settings.ChainSecond.option2 == "def"
    assert settings.ChainEnd.option3 is None

    parser = _universum.module_arguments.ModuleArgumentParser()
    define_arguments_recursive(ChainEnd, parser)
    settings = parser.parse_args([])
    assert 'ChainStart' not in dir(settings)
    assert 'ChainSecond' not in dir(settings)
    assert settings.ChainEnd.option3 is None


def test_define_arguments_mock_circular(mock_module):
    class M(mock_module):
        dep = Dependency("N")

    class N(mock_module):
        dep = Dependency('O')

    class O(mock_module):
        dep = Dependency('M')

    def assert_circular(start_module, cycle_string):
        parser = mock.MagicMock()
        with pytest.raises(LookupError) as exception_info:
            define_arguments_recursive(start_module, parser)
        assert 'Circular dependency' in str(exception_info.value)
        assert cycle_string in str(exception_info.value)

    assert_circular(M, "M->N->O->M")
    assert_circular(N, "N->O->M->N")

    O.dep = Dependency('N')
    assert_circular(M, "N->O->N")
    assert_circular(N, "N->O->N")
    assert_circular(O, "O->N->O")

    O.dep = Dependency('O')
    assert_circular(O, "O->O")


def make_settings(name, value):
    settings = _universum.module_arguments.ModuleNamespace()
    child_settings = _universum.module_arguments.ModuleNamespace()
    child_settings.option = value

    if not isinstance(name, basestring):
        name = name.__name__
    setattr(settings, name, child_settings)
    return settings


def test_construct_component(mock_module):
    class P(mock_module):
        dep = Dependency('Q')

        def __init__(self):
            self.q = 123

        def init_with_dependency(self):
            self.q = self.dep()

    class Q(mock_module):
        @staticmethod
        def define_arguments(parser):
            parser.add_argument('--option')

        def __init__(self, settings):
            self.member = settings.option

        def empty_init(self, settings):
            pass

    # no reference to Q
    p1 = construct_component('P', make_settings(Q, ""))
    assert p1.q == 123

    # create dynamic reference to Q by calling method
    p2 = construct_component('P', make_settings(Q, "def"))
    p2.init_with_dependency()
    assert p2.q.member == "def"

    # create static reference to Q by rewriting __init__
    P.__init__ = P.init_with_dependency
    p3 = construct_component('P', make_settings(Q, "ghi"))
    assert p3.q.member == "ghi"

    # remove __init__ of Q to make sure "member" is present because of __init__ call
    Q.__init__ = Q.empty_init
    p4 = construct_component('P', make_settings(Q, "jkl"))
    assert 'member' not in dir(p4.q)


def test_construct_component_wrong_param(mock_module):
    class R(mock_module):
        dep = Dependency('S')

        def __init__(self):
            self.s = self.dep()

        def init_wrong_param(self, settings):
            pass

    class S(mock_module):
        @staticmethod
        def define_arguments(parser):
            parser.add_argument('--option')

        def __init__(self, settings):
            self.member = settings.option

        def init_wrong_param(self):
            pass

    # no exception
    r1 = construct_component('R', make_settings(S, "abc"))
    assert r1.s.member == "abc"

    # exception with missing parameter in __init__
    old_init = S.__init__
    S.__init__ = S.init_wrong_param
    with pytest.raises(TypeError) as exception_info:
        construct_component('R', make_settings(S, "abc"))

    assert "S" in str(exception_info.value)

    # exception with excessive parameter in __init__
    S.__init__ = old_init
    R.__init__ = R.init_wrong_param
    with pytest.raises(TypeError) as exception_info:
        construct_component('R', make_settings(S, "abc"))

    assert "R" in str(exception_info.value)


def test_construct_component_same_instance(mock_module):
    class V(mock_module):
        pass

    class U(mock_module):
        dep = Dependency(V)

        def __init__(self):
            self.v = self.dep()

    class T(mock_module):
        dep1 = Dependency(U)
        dep2 = Dependency(V)

        def __init__(self):
            self.u = self.dep1()
            self.v = self.dep2()

    settings = _universum.module_arguments.ModuleNamespace()
    t = construct_component(T, settings)
    assert t.v == t.u.v
    assert "T" in repr(t)
    assert "U" in repr(t.u)
    assert "V" in repr(t.v)

    u = construct_component(U, settings)
    assert t.u == u

    v = construct_component(V, settings)
    assert t.v == v


def test_additional_init_parameters(mock_module):
    class W(mock_module):
        @staticmethod
        def define_arguments(parser):
            parser.add_argument('--option')

        def __init__(self, settings, param, named_parm):
            self.member1 = settings.option
            self.member2 = param
            self.member3 = named_parm

    class X(mock_module):
        dep = Dependency(W)

        def __init__(self):
            self.w = self.dep(123, named_parm=456)

    x = construct_component(X, make_settings(W, "abc"))
    assert x.w.member1 == "abc"
    assert x.w.member2 == 123
    assert x.w.member3 == 456


def parse_settings(klass, arguments):
    parser = _universum.module_arguments.ModuleArgumentParser()
    define_arguments_recursive(klass, parser)
    return parser.parse_args(arguments)


def test_construct_component_inheritance(mock_module):
    class BaseModule(mock_module):
        @staticmethod
        def define_arguments(parser):
            parser.add_argument('--base_option')

        def __init__(self, settings, base_param, **kwargs):
            self.base_option = settings.base_option
            self.base_param = base_param
            self.super_init(BaseModule, **kwargs)

    class DerivedModule(BaseModule):
        @staticmethod
        def define_arguments(parser):
            parser.add_argument('--derived_option')

        def __init__(self, settings, **kwargs):
            self.derived_option = settings.derived_option
            self.derived_member = 123
            self.super_init(DerivedModule, 456, **kwargs)

    settings = parse_settings(DerivedModule, ["--base_option=abc", "--derived_option=def"])
    assert settings.BaseModule.base_option == "abc"
    assert settings.DerivedModule.derived_option == "def"
    derived = construct_component(DerivedModule, settings)
    assert derived.base_option == "abc"
    assert derived.derived_option == "def"
    assert derived.derived_member == 123
    assert derived.base_param == 456


def test_construct_component_multiple_inheritance(mock_module):
    class Base1(mock_module):
        @staticmethod
        def define_arguments(parser):
            parser.add_argument('--base1_option')

        def __init__(self, settings, base1_param, **kwargs):
            self.base1_option = settings.base1_option
            self.base1_param = base1_param
            self.super_init(Base1, **kwargs)

    class Base2(object):
        def __init__(self, base2_param, **kwargs):
            self.base2_param = base2_param
            self.super_init(Base2, **kwargs)

    class Base3(mock_module):
        @staticmethod
        def define_arguments(parser):
            parser.add_argument('--base3_option')

        def __init__(self, settings, base3_param, **kwargs):
            self.base3_option = settings.base3_option
            self.base3_param = base3_param
            self.super_init(Base3, **kwargs)

    class Base4(mock_module):
        def __init__(self, base4_param, **kwargs):
            self.base4_param = base4_param
            self.super_init(Base4, **kwargs)

    class DerivedMulti(Base1, Base2, Base3, Base4):
        @staticmethod
        def define_arguments(parser):
            parser.add_argument('--derived_option')

        def __init__(self, settings, **kwargs):
            self.derived_option = settings.derived_option
            self.derived_member = 123
            self.super_init(DerivedMulti, base1_param="ab", base2_param="cd", base3_param="ef", base4_param="gh", **kwargs)

    local_settings = parse_settings(DerivedMulti, ["--base1_option=abc", "--base3_option=def", "--derived_option=ghi"])
    derived = construct_component(DerivedMulti, local_settings)
    assert derived.derived_option == "ghi"
    assert derived.base1_option == "abc"
    assert derived.base3_option == "def"
    assert derived.base1_param == "ab"
    assert derived.base2_param == "cd"
    assert derived.base3_param == "ef"
    assert derived.base4_param == "gh"