# pylint: disable = redefined-outer-name

import pytest
import P4
import sh

from universum import __main__
from . import utils
from .utils import python
from .perforce_utils import P4Environment


def test_which_universum_is_tested(docker_main, pytestconfig):
    config = """
from universum.configuration_support import Step, Configuration

configs = Configuration([Step(name="Check python", command=["ls", "-la"])])
"""
    # THIS TEST PATCHES ACTUAL SOURCES! Discretion is advised
    init_file = pytestconfig.rootpath.joinpath("universum", "__init__.py")
    backup = init_file.read_bytes()
    test_line = utils.randomize_name("THIS IS A TESTING VERSION")
    init_file.write_text(f"""__title__ = "Universum"
__version__ = "{test_line}"
""")
    output = docker_main.run(config, vcs_type="none")
    init_file.write_bytes(backup)
    assert test_line in output

    docker_main.environment.assert_successful_execution("pip uninstall -y universum")
    docker_main.run(config, vcs_type="none", force_installed=True, expected_to_fail=True)
    docker_main.clean_artifacts()
    docker_main.run(config, vcs_type="none")  # not expected to fail
    if utils.is_pycharm():
        docker_main.environment.install_python_module(docker_main.working_dir)


@pytest.fixture(name='print_text_on_teardown')
def fixture_print_text_on_teardown():
    yield
    print("TearDown fixture output must be handled by 'detect_fails' fixture")


def test_teardown_fixture_output_verification(print_text_on_teardown):
    pass


def test_clean_sources_exceptions(tmpdir):
    env = utils.TestEnvironment(tmpdir, "main")
    env.settings.Vcs.type = "none"
    env.settings.LocalMainVcs.source_dir = str(tmpdir / 'nonexisting_dir')

    # Check failure with non-existing temp dir
    __main__.run(env.settings)
    # the log output is automatically checked by the 'detect_fails' fixture

    # Check failure with temp dir deleted by the launched project
    env.settings.LocalMainVcs.source_dir = str(tmpdir)
    env.configs_file.write("""
from universum.configuration_support import Configuration

configs = Configuration([dict(name="Test configuration", command=["bash", "-c", "rm -rf {}"])])
""".format(env.settings.ProjectDirectory.project_root))

    __main__.run(env.settings)
    # the log output is automatically checked by the 'detect_fails' fixture


def test_non_utf8_environment(docker_main):
    # POSIX has no 'UTF-8' in it's name, but supports Unicode
    output = docker_main.run("""
from universum.configuration_support import Configuration

configs = Configuration([dict(name="Test configuration", command=["ls", "-la"])])
""", vcs_type="none", environment=['LANG=POSIX', 'LC_ALL=POSIX'])
    assert "\u2514" in output

    # 'en_US', unlike 'en_US.UTF-8', is latin-1
    docker_main.clean_artifacts()
    docker_main.environment.assert_successful_execution('apt install -y locales')
    docker_main.environment.assert_successful_execution('locale-gen --purge en_US')
    docker_main.environment.assert_successful_execution('update-locale LANG=en_US')
    output = docker_main.run("""
from universum.configuration_support import Configuration

configs = Configuration([dict(name="Test configuration", command=["ls", "-la"])])
""", vcs_type="none", environment=['LANG=en_US', 'LC_ALL=en_US'])
    assert "\u2514" not in output


@pytest.fixture()
def perforce_environment(perforce_workspace, tmpdir):
    yield P4Environment(perforce_workspace, tmpdir, test_type="main")


def test_p4_multiple_spaces_in_mappings(perforce_environment):
    perforce_environment.settings.PerforceWithMappings.project_depot_path = None
    perforce_environment.settings.PerforceWithMappings.mappings = [f"{perforce_environment.depot}   /..."]
    assert not __main__.run(perforce_environment.settings)


def shelve_config(config, perforce_environment):
    p4 = perforce_environment.p4
    p4_file = perforce_environment.repo_file
    p4.run_edit(perforce_environment.depot)
    p4_file.write(config)
    change = p4.fetch_change()
    change["Description"] = "CL for shelving"
    shelve_cl = p4.save_change(change)[0].split()[1]
    p4.run_shelve("-fc", shelve_cl)
    settings = perforce_environment.settings
    settings.PerforceMainVcs.shelve_cls = [shelve_cl]
    settings.Launcher.config_path = p4_file.basename
    return settings


def test_p4_repository_difference_format(perforce_environment):
    config = """
from universum.configuration_support import Configuration

configs = Configuration([dict(name="This is a changed step name", command=["ls", "-la"])])
"""
    settings = shelve_config(config, perforce_environment)
    result = __main__.run(settings)

    assert result == 0
    diff = perforce_environment.artifact_dir.join('REPOSITORY_DIFFERENCE.txt').read()
    assert "This is a changed step name" in diff
    assert "b'" not in diff


@pytest.fixture()
def mock_opened(monkeypatch):
    def mocking_function(*args, **kwargs):
        raise P4.P4Exception("Client 'p4_disposable_workspace' unknown - use 'client' command to create it.")

    monkeypatch.setattr(P4.P4, 'run_opened', mocking_function, raising=False)


def test_p4_failed_opened(perforce_environment, mock_opened):
    assert not __main__.run(perforce_environment.settings)


# TODO: move this test to 'test_api.py' after test refactoring and Docker use reduction
def test_p4_api_failed_opened(perforce_environment, mock_opened):
    step_name = "API"
    config = f"""
from universum.configuration_support import Step, Configuration

configs = Configuration([Step(name="{step_name}", artifacts="output.json",
                              command=["bash", "-c", "{python()} -m universum api file-diff > output.json"])])
    """
    settings = shelve_config(config, perforce_environment)
    settings.Launcher.output = "file"

    assert not __main__.run(settings)
    log = perforce_environment.artifact_dir.join(f'{step_name}_log.txt').read()
    assert "Module sh got exit code 1" in log
    assert "Getting file diff failed due to Perforce server internal error" in log


def test_p4_clean_empty_cl(perforce_environment, stdout_checker):
    # This test creates an empty CL, triggering "file(s) not opened on this client" exception on cleanup
    # Wrong exception handling prevented further client cleanup on force clean, making final client deleting impossible

    config = f"""
from universum.configuration_support import Step, Configuration

configs = Configuration([Step(name="Create empty CL",
                              command=["bash", "-c",
                              "p4 --field 'Description=My pending change' --field 'Files=' change -o | p4 change -i"],
                              environment = {{"P4CLIENT": "{perforce_environment.client_name}",
                                              "P4PORT": "{perforce_environment.p4.port}",
                                              "P4USER": "{perforce_environment.p4.user}",
                                              "P4PASSWD": "{perforce_environment.p4.password}"}})])
"""
    settings = shelve_config(config, perforce_environment)
    assert not __main__.run(settings)
    error_message = f"""[Error]: "Client '{perforce_environment.client_name}' has pending changes."""
    stdout_checker.assert_absent_calls_with_param(error_message)


@pytest.fixture()
def mock_diff(monkeypatch):
    def mocking_function(*args, **kwargs):
        raise sh.ErrorReturnCode(stderr=b"This is error text\n F\xc3\xb8\xc3\xb6\xbbB\xc3\xa5r",
                                 stdout=b"This is text'",
                                 full_cmd="any shell call with any params")

    monkeypatch.setattr(sh, 'Command', mocking_function, raising=False)


def test_p4_diff_exception_handling(perforce_environment, mock_diff, stdout_checker):
    config = """
from universum.configuration_support import Step, Configuration

configs = Configuration([Step(name="Step", command=["ls"])])
"""
    settings = shelve_config(config, perforce_environment)
    assert __main__.run(settings)
    stdout_checker.assert_has_calls_with_param("This is error text")
    # Without the fixes all error messages go to stderr instead of stdout
