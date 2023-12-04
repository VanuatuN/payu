"""Experiment branch support for payu's branch, clone and checkout commands

This may generate new experiment ID, updates, sets any
specified configuration in config.yaml and updates work/archive symlinks

:copyright: Copyright 2011 Marshall Ward, see AUTHORS for details.
:license: Apache License, Version 2.0, see LICENSE for details.
"""

import os
import warnings
from pathlib import Path
from typing import Optional

from ruamel.yaml import YAML
import git

from payu.fsops import read_config, DEFAULT_CONFIG_FNAME
from payu.laboratory import Laboratory
from payu.metadata import Metadata, UUID_FIELD
from payu.git_utils import git_checkout_branch, git_clone, get_git_branch
from payu.git_utils import get_git_repository
from payu.git_utils import remote_branches_dict, local_branches_dict

NO_CONFIG_FOUND_MESSAGE = """No configuration file found on this branch.
Skipping adding new metadata file and creating archive/work symlinks.

To try find a branch that has config file, you can:
    - Display local branches by running:
        payu branch
    - Or display remote branches by running:
        payu branch --remote

To checkout an existing branch, run:
    payu checkout BRANCH_NAME
Where BRANCH_NAME is the name of the branch"""


def add_restart_to_config(restart_path: Path,
                          config_path: Path) -> None:
    """Takes restart path and config path, and add 'restart' flag to the
    config file - which is used to start a run if there isn't a pre-existing
    restart in archive"""

    # Check for valid paths
    if not restart_path.exists() or not restart_path.is_dir():
        warnings.warn((f"Given restart directory {restart_path} does not "
                       f"exist. Skipping adding 'restart: {restart_path}' "
                       "to config file"))
        return

    # Default ruamel yaml preserves comments and multiline strings
    yaml = YAML()
    config = yaml.load(config_path)

    # Add in restart path
    config['restart'] = str(restart_path)

    # Write modified lines back to config
    yaml.dump(config, config_path)
    print(f"Added 'restart: {restart_path}' to configuration file:",
          config_path.name)


def get_control_path(config_path: Path) -> Path:
    """Given the config path, return the control path"""
    # Note: Control path is set in read_config
    config = read_config(config_path)
    return Path(config.get('control_path'))


def check_config_path(config_path: Optional[Path] = None) -> Optional[Path]:
    """Checks if configuration file exists"""
    if config_path is None:
        config_path = Path(DEFAULT_CONFIG_FNAME)
        config_path.resolve()

    if not config_path.exists() or not config_path.is_file:
        print(NO_CONFIG_FOUND_MESSAGE)
        raise FileNotFoundError(f"Configuration file {config_path} not found")

    return config_path


def checkout_branch(branch_name: str,
                    is_new_branch: bool = False,
                    is_new_experiment: bool = False,
                    keep_uuid: bool = False,
                    start_point: Optional[str] = None,
                    restart_path: Optional[Path] = None,
                    config_path: Optional[Path] = None,
                    control_path: Optional[Path] = None,
                    model_type: Optional[str] = None,
                    lab_path: Optional[Path] = None) -> None:
    """Checkout branch, setup metadata and add symlinks

    Parameters
    ----------
    branch_name : str
        Name of branch to checkout/create
    is_new_branch: bool, default False
        Create new branch and mark as new experiment
    is_new_experiment: bool, default False
        Create new uuid for this experiment
    keep_uuid: bool, default False
        Keep UUID unchanged, if it exists - this overrides is_new_experiment
        if there is a pre-existing UUID
    start_point: Optional[str], default None
        Branch name or commit hash to start new branch from
    restart_path: Optional[Path], default None
        Absolute restart path to start experiment from
    config_path: Optional[Path], default None
        Path to configuration file - config.yaml
    control_path: Optional[Path], default None
        Path to control directory - defaults to current working directory
    model_type: Optional[str], default None
        Type of model - used for creating a Laboratory
    lab_path: Optional[Path], default None
        Path to laboratory directory
    """
    if control_path is None:
        control_path = get_control_path(config_path)

    # Checkout branch
    git_checkout_branch(control_path, branch_name, is_new_branch, start_point)

    # Check config file exists on checked out branch
    config_path = check_config_path(config_path)

    # Initialise Lab and Metadata
    lab = Laboratory(model_type, config_path, lab_path)
    metadata = Metadata(Path(lab.archive_path),
                        branch=branch_name,
                        config_path=config_path)

    # Setup Metadata
    is_new_experiment = is_new_experiment or is_new_branch
    metadata.setup(keep_uuid=keep_uuid, is_new_experiment=is_new_experiment)

    # Add restart option to config
    if restart_path:
        add_restart_to_config(restart_path, config_path=config_path)

    # Switch/Remove/Add archive and work symlinks
    experiment = metadata.experiment_name
    switch_symlink(Path(lab.archive_path), control_path, experiment, 'archive')
    switch_symlink(Path(lab.work_path), control_path, experiment, 'work')


def switch_symlink(lab_dir_path: Path, control_path: Path,
                   experiment_name: str, sym_dir: str) -> None:
    """Helper function for removing and switching work and archive
    symlinks in control directory"""
    dir_path = lab_dir_path / experiment_name
    sym_path = control_path / sym_dir

    # Remove symlink if it already exists
    if sym_path.exists() and sym_path.is_symlink:
        previous_path = sym_path.resolve()
        sym_path.unlink()
        print(f"Removed {sym_dir} symlink to {previous_path}")

    # Create symlink, if experiment directory exists in laboratory
    if dir_path.exists():
        sym_path.symlink_to(dir_path)
        print(f"Added {sym_dir} symlink to {dir_path}")


def clone(repository: str,
          directory: Path,
          branch: Optional[str] = None,
          new_branch_name: Optional[str] = None,
          keep_uuid: bool = False,
          model_type: Optional[str] = None,
          config_path: Optional[Path] = None,
          lab_path: Optional[Path] = None,
          restart_path: Optional[Path] = None) -> None:
    """Clone an experiment control repository.

    Parameters:
        repository: str
            Git URL or path to Git repository to clone
        directory: Path
            The control directory where the repository will be cloned
        branch: Optional[str]
            Name of branch to clone and checkout
        new_branch_name: Optional[str]
            Name of new branch to create and checkout.
            If branch is also defined, the new branch will start from the
            latest commit of the branch.
        keep_uuid: bool, default False
            Keep UUID unchanged, if it exists
        config_path: Optional[Path]
            Path to configuration file - config.yaml
        control_path: Optional[Path]
            Path to control directory - defaults to current working directory
        model_type: Optional[str]
            Type of model - used for creating a Laboratory
        lab_path: Optional[Path]
            Path to laboratory directory
        restart_path: Optional[Path]
            Absolute restart path to start experiment from

    Returns: None
    """
    # git clone the repository
    git_clone(repository, directory, branch)

    # Resolve directory to an absolute path
    control_path = directory.resolve()

    owd = os.getcwd()
    try:
        # cd into cloned directory
        os.chdir(control_path)

        # Use checkout wrapper
        if new_branch_name is not None:
            # Create and checkout new branch
            checkout_branch(is_new_branch=True,
                            keep_uuid=keep_uuid,
                            branch_name=new_branch_name,
                            restart_path=restart_path,
                            config_path=config_path,
                            control_path=control_path,
                            model_type=model_type,
                            lab_path=lab_path)
        else:
            # Checkout branch
            if branch is None:
                branch = get_git_branch(control_path)

            checkout_branch(branch_name=branch,
                            config_path=config_path,
                            keep_uuid=keep_uuid,
                            restart_path=restart_path,
                            control_path=control_path,
                            model_type=model_type,
                            lab_path=lab_path,
                            is_new_experiment=True)
    finally:
        # Change back to original working directory
        os.chdir(owd)

    print(f"To change directory to control directory run:\n  cd {directory}")


def print_branch_metadata(branch: git.Head, verbose: bool = False):
    """Display given Git branch UUID, or if config.yaml or metadata.yaml does
    not exist.

    Parameters:
        branch: git.Head
            Branch object to parse commit tree.
        verbose: bool, default False
            Display entire metadata files
        remote: bool, default False
            Display remote Git branches

    Returns: None
    """
    contains_config = False
    metadata_content = None
    # Note: Blobs are files in the commit tree
    for blob in branch.commit.tree.blobs:
        if blob.name == 'config.yaml':
            contains_config = True
        if blob.name == 'metadata.yaml':
            # Read file contents
            metadata_content = blob.data_stream.read().decode('utf-8')

    # Print branch info
    if not contains_config:
        print(f"    No config file found")
    elif metadata_content is None:
        print("    No metadata file found")
    else:
        if verbose:
            # Print all metadata
            for line in metadata_content.splitlines():
                print(f'    {line}')
        else:
            # Print uuid
            metadata = YAML().load(metadata_content)
            uuid = metadata.get(UUID_FIELD, None)
            if uuid is not None:
                print(f"    {UUID_FIELD}: {uuid}")
            else:
                print(f"    No UUID in metadata file")


def list_branches(config_path: Optional[Path] = None,
                  verbose: bool = False,
                  remote: bool = False):
    """Display local Git branches UUIDs.

    Parameters:
        verbose: bool, default False
            Display entire metadata files
        remote: bool, default False
            Display remote Git branches

    Returns: None"""
    control_path = get_control_path(config_path)
    repo = get_git_repository(control_path)

    current_branch = repo.active_branch
    print(f"* Current Branch: {current_branch.name}")
    print_branch_metadata(current_branch, verbose)

    if remote:
        branches = remote_branches_dict(repo)
        label = "Remote Branch"
    else:
        branches = local_branches_dict(repo)
        label = "Branch"

    for branch_name, branch in branches.items():
        if branch != current_branch:
            print(f"{label}: {branch_name}")
            print_branch_metadata(branch, verbose)
