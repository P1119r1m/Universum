# -*- coding: UTF-8 -*-

import os
import shutil
import sh

from . import git_vcs, gerrit_vcs, perforce_vcs, local_vcs, base_vcs
from .. import artifact_collector, utils
from ..gravity import Dependency, Module
from ..project_directory import ProjectDirectory
from ..structure_handler import needs_structure
from ..utils import make_block

__all__ = [
    "DownloadVcs",
    "PollVcs",
    "SubmitVcs"
]


@needs_structure
class Vcs(ProjectDirectory):
    @staticmethod
    def define_arguments(argument_parser):
        parser = argument_parser.get_or_create_group("Source files")

        parser.add_argument("--vcs-type", "-vt", dest="type", default="p4",
                            choices=["none", "p4", "git", "gerrit"],
                            help="Select repository type to download sources from: Perforce ('p4', the default), "
                                 "Git ('git'), Gerrit ('gerrit') or a local directory ('none'). "
                                 "Gerrit uses Git parameters. Each VCS type has its own settings.")

    def __init__(self, *args, **kwargs):
        super(Vcs, self).__init__(*args, **kwargs)
        try:
            if self.settings.type == "none":
                self.driver = self.local_driver_factory()
            elif self.settings.type == "git":
                self.driver = self.git_driver_factory()
            elif self.settings.type == "gerrit":
                self.driver = self.gerrit_driver_factory()
            else:
                self.driver = self.perforce_driver_factory()
        except AttributeError:
            raise NotImplementedError()

    @make_block("Finalizing")
    def finalize(self):
        self.driver.finalize()


def create_vcs(name=None):
    if name == "DownloadVcs":
        p4_driver_factory_class = perforce_vcs.PerforceDownloadVcs
        git_driver_factory_class = git_vcs.GitDownloadVcs
        gerrit_driver_factory_class = gerrit_vcs.GerritDownloadVcs
        local_driver_factory_class = local_vcs.LocalDownloadVcs
    elif name == "SubmitVcs":
        p4_driver_factory_class = perforce_vcs.PerforceSubmitVcs
        git_driver_factory_class = git_vcs.GitSubmitVcs
        gerrit_driver_factory_class = gerrit_vcs.GerritSubmitVcs
        local_driver_factory_class = base_vcs.BaseSubmitVcs
    elif name == "PollVcs":
        p4_driver_factory_class = perforce_vcs.PerforcePollVcs
        git_driver_factory_class = git_vcs.GitPollVcs
        gerrit_driver_factory_class = git_vcs.GitPollVcs
        local_driver_factory_class = base_vcs.BasePollVcs
    else:
        raise NotImplementedError()

    class MixIn(Module):
        local_driver_factory = Dependency(local_driver_factory_class)
        git_driver_factory = Dependency(git_driver_factory_class)
        gerrit_driver_factory = Dependency(gerrit_driver_factory_class)
        perforce_driver_factory = Dependency(p4_driver_factory_class)

        @staticmethod
        def define_arguments(argument_parser):
            pass  # TODO: refactor gravity to remove this

    MixIn.__name__ = name + "Template"
    return MixIn


class PollVcs(Vcs, create_vcs("PollVcs")):
    @staticmethod
    def define_arguments(argument_parser):
        pass # TODO: refactor gravity to remove this


class SubmitVcs(Vcs, create_vcs("SubmitVcs")):
    @staticmethod
    def define_arguments(argument_parser):
        pass  # TODO: refactor gravity to remove this


class DownloadVcs(Vcs, create_vcs("DownloadVcs")):
    artifacts_factory = Dependency(artifact_collector.ArtifactCollector)

    @staticmethod
    def define_arguments(argument_parser):
        parser = argument_parser.get_or_create_group("Source files")

        parser.add_argument("--report-to-review", action="store_true", dest="report_to_review", default=False,
                            help="Perform test build for code review system (e.g. Gerrit or Swarm).")

    def __init__(self, *args, **kwargs):
        super(DownloadVcs, self).__init__(*args, **kwargs)
        self.artifacts = self.artifacts_factory()

        if self.settings.report_to_review:
            self.code_review = self.driver.code_review()

    def is_latest_review_version(self):
        if self.settings.report_to_review:
            return self.code_review.is_latest_version()
        return True

    @make_block("Preparing repository")
    def prepare_repository(self):
        status_file = self.artifacts.create_text_file("REPOSITORY_STATE.txt")

        self.driver.prepare_repository()

        status_file.write(self.driver.get_repo_status())

        status_file.write("\nFile list:\n\n")
        status_file.write(utils.trim_and_convert_to_unicode(sh.ls("-lR", self.settings.project_root)) + "\n")
        status_file.close()

    def clean_sources_silently(self):
        try:
            shutil.rmtree(self.settings.project_root)
        except OSError:
            pass
        os.makedirs(self.settings.project_root)
