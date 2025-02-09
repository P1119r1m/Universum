# pylint: disable = redefined-outer-name

import copy
import os
import shutil
import pytest

from universum import __main__
from . import git_utils, perforce_utils, utils


def test_error_no_repo(submit_environment, stdout_checker):
    settings = copy.deepcopy(submit_environment.settings)
    if settings.Vcs.type == "git":
        settings.ProjectDirectory.project_root = "non_existing_repo"
        __main__.run(settings)
        stdout_checker.assert_has_calls_with_param("No such directory")
    else:
        settings.PerforceSubmitVcs.client = "non_existing_client"
        __main__.run(settings)
        stdout_checker.assert_has_calls_with_param("Workspace 'non_existing_client' doesn't exist!")


@pytest.fixture()
def p4_submit_environment(perforce_workspace, tmpdir):
    yield perforce_utils.P4Environment(perforce_workspace, tmpdir, test_type="submit")


@pytest.mark.parametrize("branch", ["write-protected", "trigger-protected"])
def test_p4_error_forbidden_branch(p4_submit_environment, branch):
    protected_dir = p4_submit_environment.vcs_cooking_dir.mkdir(branch)
    file_to_add = protected_dir.join(utils.randomize_name("new_file") + ".txt")
    text = "This is a new line in the file"
    file_to_add.write(text + "\n")

    settings = copy.deepcopy(p4_submit_environment.settings)
    setattr(settings.Submit, "reconcile_list", str(file_to_add))

    assert __main__.run(settings)

    p4 = p4_submit_environment.p4
    # make sure submitter didn't leave any pending CLs in the workspace
    assert not p4.run_changes("-c", p4_submit_environment.client_name, "-s", "pending")
    # make sure submitter didn't leave any pending changes in default CL
    assert not p4.run_opened("-C", p4_submit_environment.client_name)


def test_p4_success_files_in_default(p4_submit_environment):
    # This file should not be submitted, it should remain unchanged in default CL
    p4 = p4_submit_environment.p4
    p4_file = p4_submit_environment.repo_file
    p4.run_edit(str(p4_file))
    text = "This text should be in file"
    p4_file.write(text + "\n")

    # This file should be successfully submitted
    file_name = utils.randomize_name("new_file") + ".txt"
    new_file = p4_submit_environment.vcs_cooking_dir.join(file_name)
    new_file.write("This is a new file" + "\n")

    settings = copy.deepcopy(p4_submit_environment.settings)
    setattr(settings.Submit, "reconcile_list", str(new_file))

    assert not __main__.run(settings)
    assert text in p4_file.read()


def test_p4_error_files_in_default_and_reverted(p4_submit_environment):
    # This file should not be submitted, it should remain unchanged in default CL
    p4 = p4_submit_environment.p4
    p4_file = p4_submit_environment.repo_file
    p4.run_edit(str(p4_file))
    text_default = "This text should be in file"
    p4_file.write(text_default + "\n")

    # This file must fail submit and remain unchanged while not checked out any more
    protected_dir = p4_submit_environment.vcs_cooking_dir.mkdir("write-protected")
    new_file = protected_dir.join(utils.randomize_name("new_file") + ".txt")
    text_new = "This is a new line in the file"
    new_file.write(text_new + "\n")

    settings = copy.deepcopy(p4_submit_environment.settings)
    setattr(settings.Submit, "reconcile_list", str(new_file))

    assert __main__.run(settings)
    assert text_default in p4_file.read()
    assert text_new in new_file.read()


class SubmitterParameters:
    def __init__(self, stdout_checker, environment):
        self.stdout_checker = stdout_checker
        self.submit_settings = environment.settings
        self.environment = environment

    def submit_path_list(self, path_list, **kwargs):
        settings = copy.deepcopy(self.submit_settings)
        setattr(settings.Submit, "reconcile_list", ",".join(path_list))

        if kwargs:
            for key, value in kwargs.items():
                setattr(settings.Submit, key, value)

        return __main__.run(settings)

    def assert_submit_success(self, path_list, **kwargs):
        result = self.submit_path_list(path_list, **kwargs)
        assert result == 0

        last_cl = self.environment.get_last_change()
        self.stdout_checker.assert_has_calls_with_param("==> Change " + last_cl + " submitted")

    def file_present(self, file_path):
        return self.environment.file_present(file_path)

    def text_in_file(self, text, file_path):
        return self.environment.text_in_file(text, file_path)


@pytest.fixture()
def submit_parameters(stdout_checker):
    def inner(environment):
        return SubmitterParameters(stdout_checker, environment)
    yield inner


@pytest.fixture(params=["git", "p4"])
def submit_environment(request, perforce_workspace, git_client, tmpdir):
    if request.param == "git":
        yield git_utils.GitEnvironment(git_client, tmpdir, test_type="submit")
    else:
        yield perforce_utils.P4Environment(perforce_workspace, tmpdir, test_type="submit")


def test_success_no_changes(submit_parameters, submit_environment):
    parameters = submit_parameters(submit_environment)
    assert parameters.submit_path_list([]) == 0


def test_success_commit_add_modify_remove_one_file(submit_parameters, submit_environment):
    parameters = submit_parameters(submit_environment)

    file_name = utils.randomize_name("new_file") + ".txt"
    temp_file = parameters.environment.vcs_cooking_dir.join(file_name)
    file_path = str(temp_file)

    # Add a file
    temp_file.write("This is a new file" + "\n")
    parameters.assert_submit_success([file_path])
    assert parameters.file_present(file_path)

    # Modify a file
    text = "This is a new line in the file"
    temp_file.write(text + "\n")
    parameters.assert_submit_success([file_path])
    assert parameters.text_in_file(text, file_path)

    # Delete a file
    temp_file.remove()
    parameters.assert_submit_success([file_path])
    assert not parameters.file_present(file_path)


def test_success_ignore_new_and_deleted_while_edit_only(submit_parameters, submit_environment):
    parameters = submit_parameters(submit_environment)

    new_file_name = utils.randomize_name("new_file") + ".txt"
    temp_file = parameters.environment.vcs_cooking_dir.join(new_file_name)
    temp_file.write("This is a new temp file" + "\n")
    deleted_file_path = str(parameters.environment.repo_file)
    deleted_file_name = os.path.basename(deleted_file_path)
    os.remove(deleted_file_path)

    result = parameters.submit_path_list([str(temp_file), deleted_file_path], edit_only=True)
    assert result == 0

    parameters.stdout_checker.assert_has_calls_with_param(f"Skipping '{new_file_name}'")
    parameters.stdout_checker.assert_has_calls_with_param(f"Skipping '{deleted_file_name}'")
    parameters.stdout_checker.assert_has_calls_with_param("Nothing to submit")
    assert parameters.file_present(deleted_file_path)
    assert not parameters.file_present(str(temp_file))


def test_success_commit_modified_while_edit_only(submit_parameters, submit_environment):
    parameters = submit_parameters(submit_environment)

    target_file = parameters.environment.repo_file
    text = utils.randomize_name("This is change ")
    target_file.write(text + "\n")

    parameters.assert_submit_success([str(target_file)], edit_only=True)
    assert parameters.text_in_file(text, str(target_file))


def test_error_review(submit_parameters, submit_environment):
    parameters = submit_parameters(submit_environment)

    target_file = parameters.environment.repo_file
    target_file.write("This is some change")

    result = parameters.submit_path_list([str(target_file)], review=True)
    assert result != 0
    parameters.stdout_checker.assert_has_calls_with_param("not supported")


def test_success_reconcile_directory(submit_parameters, submit_environment):
    parameters = submit_parameters(submit_environment)

    dir_name = utils.randomize_name("new_directory")

    # Create and reconcile new directory
    tmp_dir = parameters.environment.vcs_cooking_dir.mkdir(dir_name)
    for i in range(0, 9):
        tmp_file = tmp_dir.join(f"new_file{i}.txt")
        tmp_file.write("This is some file" + "\n")

    parameters.assert_submit_success([str(tmp_dir) + "/"])

    for i in range(0, 9):
        file_path = tmp_dir.join(f"new_file{i}.txt")
        assert parameters.file_present(str(file_path))

    # Create and reconcile a directory in a directory
    another_dir = tmp_dir.mkdir("another_directory")
    tmp_file = another_dir.join("new_file.txt")
    tmp_file.write("This is some file" + "\n")

    parameters.assert_submit_success([str(tmp_dir) + "/"])
    assert parameters.file_present(str(tmp_file))

    # Modify some vcs
    text = utils.randomize_name("This is change ")
    for i in range(0, 9, 2):
        tmp_file = tmp_dir.join(f"new_file{i}.txt")
        tmp_file.write(text + "\n")

    parameters.assert_submit_success([str(tmp_dir) + "/"], edit_only=True)

    for i in range(0, 9, 2):
        file_path = tmp_dir.join(f"/new_file{i}.txt")
        assert parameters.text_in_file(text, str(file_path))

    # Delete a directory
    shutil.rmtree(tmp_dir)
    parameters.assert_submit_success([str(tmp_dir)])
    assert not parameters.file_present(str(tmp_dir))


def test_success_reconcile_wildcard(submit_parameters, submit_environment):
    parameters = submit_parameters(submit_environment)

    dir_name = utils.randomize_name("new_directory")

    # Create embedded directories, partially reconcile
    tmp_dir = parameters.environment.vcs_cooking_dir.mkdir(dir_name)
    inner_dir = tmp_dir.mkdir("inner_directory")
    text = "This is some file" + "\n"
    for i in range(0, 9):
        tmp_file = tmp_dir.join(f"new_file{i}.txt")
        tmp_file.write(text)
        tmp_file = tmp_dir.join(f"another_file{i}.txt")
        tmp_file.write(text)
        tmp_file = inner_dir.join(f"new_file{i}.txt")
        tmp_file.write(text)

    parameters.assert_submit_success([str(tmp_dir) + "/new_file*.txt"])

    for i in range(0, 9):
        file_name = f"new_file{i}.txt"
        file_path = tmp_dir.join(file_name)
        assert parameters.file_present(str(file_path))
        file_path = inner_dir.join(file_name)
        assert not parameters.file_present(str(file_path))
        file_name = f"another_file{i}.txt"
        file_path = tmp_dir.join(file_name)
        assert not parameters.file_present(str(file_path))

    # Create one more directory
    other_dir_name = utils.randomize_name("new_directory")
    other_tmp_dir = parameters.environment.vcs_cooking_dir.mkdir(other_dir_name)
    for i in range(0, 9):
        tmp_file = other_tmp_dir.join(f"new_file{i}.txt")
        tmp_file.write("This is some file" + "\n")

    parameters.assert_submit_success([str(parameters.environment.vcs_cooking_dir) + "/new_directory*/"])

    for i in range(0, 9):
        file_name = f"new_file{i}.txt"
        file_path = other_tmp_dir.join(file_name)
        assert parameters.file_present(str(file_path))
        file_path = inner_dir.join(file_name)
        assert parameters.file_present(str(file_path))
        file_name = f"another_file{i}.txt"
        file_path = tmp_dir.join(file_name)
        assert parameters.file_present(str(file_path))

    # Modify some vcs
    text = utils.randomize_name("This is change ")
    for i in range(0, 9, 2):
        tmp_file = tmp_dir.join(f"new_file{i}.txt")
        tmp_file.write(text + "\n")
        tmp_file = inner_dir.join(f"new_file{i}.txt")
        tmp_file.write(text + "\n")
        tmp_file = tmp_dir.join(f"another_file{i}.txt")
        tmp_file.write(text + "\n")

    parameters.assert_submit_success([str(tmp_dir) + "/new_file*.txt"], edit_only=True)

    for i in range(0, 9, 2):
        file_path = tmp_dir.join(f"/new_file{i}.txt")
        assert parameters.text_in_file(text, str(file_path))
        file_path = inner_dir.join(f"/new_file{i}.txt")
        assert not parameters.text_in_file(text, str(file_path))
        file_path = tmp_dir.join(f"/another_file{i}.txt")
        assert not parameters.text_in_file(text, str(file_path))

    # Test subdirectory wildcard
    text = utils.randomize_name("This is change ")
    for i in range(1, 9, 2):
        tmp_file = tmp_dir.join(f"new_file{i}.txt")
        tmp_file.write(text + "\n")
        tmp_file = inner_dir.join(f"new_file{i}.txt")
        tmp_file.write(text + "\n")
        tmp_file = tmp_dir.join(f"another_file{i}.txt")
        tmp_file.write(text + "\n")

    parameters.assert_submit_success([str(tmp_dir) + "/*/*.txt"])

    for i in range(1, 9, 2):
        file_path = inner_dir.join(f"new_file{i}.txt")
        assert parameters.text_in_file(text, str(file_path))
        file_path = tmp_dir.join(f"new_file{i}.txt")
        assert not parameters.text_in_file(text, str(file_path))
        file_path = tmp_dir.join(f"another_file{i}.txt")
        assert not parameters.text_in_file(text, str(file_path))

    # Test edit-only subdirectory wildcard
    text = utils.randomize_name("This is change ")
    for i in range(0, 9, 3):
        tmp_file = tmp_dir.join(f"new_file{i}.txt")
        tmp_file.write(text + "\n")
        tmp_file = inner_dir.join(f"new_file{i}.txt")
        tmp_file.write(text + "\n")
        tmp_file = tmp_dir.join("another_file{i}.txt")
        tmp_file.write(text + "\n")

        parameters.assert_submit_success([str(tmp_dir) + "/*/*.txt"], edit_only=True)

    for i in range(0, 9, 3):
        file_path = inner_dir.join(f"new_file{i}.txt")
        assert parameters.text_in_file(text, str(file_path))
        file_path = tmp_dir.join(f"new_file{i}.txt")
        assert not parameters.text_in_file(text, str(file_path))
        file_path = tmp_dir.join(f"another_file{i}.txt")
        assert not parameters.text_in_file(text, str(file_path))

    # Clean up the repo
    shutil.rmtree(str(tmp_dir))
    shutil.rmtree(str(other_tmp_dir))
    parameters.assert_submit_success([str(parameters.environment.vcs_cooking_dir) + "/*"])
    assert not parameters.file_present(str(tmp_dir))
    assert not parameters.file_present(str(other_tmp_dir))


def test_success_reconcile_partial(submit_parameters, submit_environment):
    # This test was failed when a bug in univesrum.lib.utils.unify_argument_list left empty entries in processed lists
    # When reconciling "", p4 adds to CL all changes made in scope of workspace (and therefore partial reconcile fails)

    parameters = submit_parameters(submit_environment)
    dir_name = utils.randomize_name("new_directory")
    tmp_dir = parameters.environment.vcs_cooking_dir.mkdir(dir_name)
    for i in range(0, 9):
        tmp_file = tmp_dir.join(f"new_file{i}.txt")
        tmp_file.write("This is some file" + "\n")

    reconcile_list = [str(tmp_dir.join(f"new_file{i}.txt")) for i in range(0, 4)]
    reconcile_list.extend(["", " ", "\n"])
    parameters.assert_submit_success(reconcile_list)

    for i in range(0, 4):
        file_path = tmp_dir.join(f"new_file{i}.txt")
        assert parameters.file_present(str(file_path))

    for i in range(5, 9):
        file_path = tmp_dir.join(f"new_file{i}.txt")
        assert not parameters.file_present(str(file_path))

    # Delete a directory
    shutil.rmtree(tmp_dir)
    parameters.assert_submit_success([str(tmp_dir)])
    assert not parameters.file_present(str(tmp_dir))
