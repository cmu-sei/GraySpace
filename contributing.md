# Contribute to the GraySpace Project

If you’re reading this, you’re likely interested in contributing to the GraySpace
project. That’s awesome!!

Here's a checklist to get your code in tip-top shape prior to merging with the
GraySpace main branch.

## GraySpace Development Checklist

- [ ] [Choose a descriptive branch name](#choose-a-descriptive-branch-name)
- [ ] [Commit as often as you like](#commit-as-often-as-you-like)
- [ ] [Follow GraySpace code conventions](#follow-grayspace-code-conventions)
- [ ] [Check for large files not in LFS](#check-for-large-files-not-in-lfs)
- [ ] [Before submitting merge request, squash and rebase](#before-submitting-merge-request-squash-and-rebase)
- [ ] [Write helpful commit message](#write-helpful-commit-message)
- [ ] [Create merge request in GitHub](#create-merge-request-in-GitHub)

### Choose a descriptive branch name

If there’s a GitHub issue associated with the branch, please include
that identifier at the beginning of the branch name. For example:

- `42-add-latency-to-links-based-on-location-data`

**NOTE:** Smaller GraySpace updates for specific features/bug fixes are preferred over long
lasting branches that become harder to maintain and merge with the existing code
base.

### Commit as often as you like

While developing your feature branch, you can push changes to that branch as
often as you like (more is better if you don’t want to lose any work). Feel free
to use short, one-line commit messages during development. The important thing
is that you can remember what you’ve done so you can write a summary commit
message when merging your code.

### Follow GraySpace code conventions

Here are some GraySpace code conventions that should be followed when submitting
code to the project:

- Spaces instead of tabs
- 4 spaces for .MD indents, 2 for everything else (Python, PowerShell, etc)
- When in doubt, follow these [Ansible best practices](https://docs.ansible.com/ansible/latest/user_guide/playbooks_best_practices.html).

### Check for large files not in LFS

GraySpace uses [Git Large File Storage (LFS)](https://git-lfs.github.com/) to store
large files (.iso, .exe, etc.) without expanding the size of the main
repository. Sometimes you may inadvertently commit a large file that doesn’t
match an existing LFS extension (those extensions are listed in
**/.gitattributes**). You can check to see if there are any large files with the
following command:

      `git lfs migrate info --everything`

### Before submitting merge request, squash and rebase

The GraySpace project prefers single-commit branches when merging into the current
main branch for improved code readability. You can rebase your feature branch
against the latest main branch with the following commands:

    # Make sure you have the latest main branch
    $ git checkout main
    $ git pull

    # Switch to your feature branch and rebase
    $ git checkout <your branch name>
    $ git rebase -i main

    # Push your changes to GitHub
    $ git push -f

While performing an interactive rebase (using the -i option above), you can
squash your development commits down to a single commit, and Git will ask you
for a consolidated commit message at the end. For more information, here’s [a
deeper dive into the rebase
process](https://hackernoon.com/mastering-git-why-rebase-is-amazing-a954485b128a).

### Write helpful commit message

Because GraySpace consolidates all development work into one commit per feature, the
contents of that commit message are important.

We’ve adopted a Git commit style from Chris Beams [in this blog post](https://chris.beams.io/posts/git-commit/). Here are the rules:

1. [Separate subject from body with a blank
   line](https://chris.beams.io/posts/git-commit/#separate)
2. [Limit the subject line to 50
   characters](https://chris.beams.io/posts/git-commit/#limit-50)
3. [Capitalize the subject
   line](https://chris.beams.io/posts/git-commit/#capitalize)
4. [Do not end the subject line with a
   period](https://chris.beams.io/posts/git-commit/#end)
5. [Use the imperative mood in the subject
   line](https://chris.beams.io/posts/git-commit/#imperative)
6. [Wrap the body at 72
   characters](https://chris.beams.io/posts/git-commit/#wrap-72)
7. [Use the body to explain what and why vs.
   how](https://chris.beams.io/posts/git-commit/#why-not-how)

**NOTE:** Include details on how the code has been **tested**--environment used, test procedure, etc.

#### Example commit message

    Adds latency to router links based on data model

    Longer description of latency model addition. Tested latency modeling in
    a dev container and on bare metal Proxmox.

    Focus on answering questions of what you changed and why you changed it.
    And please wrap these lines at 72 characters so that viewing with `git
    log` can be done without running to the edge of the terminal window.

    - You can also include bullets in the body of your commit message.
    - It’s completely up to you as far as style goes.
    - Vim should help you with the line wrapping (subject and body).

    Closes #42

### Create merge request in GitHub

If you’ve done everything above, this last step is a piece of cake. Click the
**Create Merge Request** at the top of the project page and it should
auto-populate the fields with the contents of your commit message.

That’s it! You’re ready to start committing code to the GraySpace project. If you
have any questions, contact the project maintainer at <rlehman@cert.org>.
