# Phase 1 reproduction-audit image.
#
# Mirrors the Dockerfile of reproducing-research-projects/BugsInPy, with these changes:
#   * the base image is pinned by digest (the fork pins only the tag);
#   * the apt packages the fork names are pinned to exact versions;
#   * ccache is added (approved for the pandas compile path; it does not change build output);
#   * the build context is `git archive` of the fork at a pinned SHA (see execution/audit/image.py),
#     never a working tree.
FROM continuumio/miniconda3:23.3.1-0@sha256:f3637fcc44fac7c20aebcc6fb8910cf76139ff2f4e6a7777d7643460bee50922

ARG BUGSINPY_FORK_SHA
LABEL org.fda.bugsinpy_fork_sha="${BUGSINPY_FORK_SHA}" \
      org.fda.purpose="phase1-reproduction-audit"

# apt is pinned to a dated snapshot.debian.org archive. On 2026-09-15 the live bullseye-security
# mirror returned 404 for packages listed in its own index (bullseye LTS ended Aug 2026), so an
# unpinned `apt-get update` is no longer reproducible.
ARG DEBIAN_SNAPSHOT=20260901T000000Z
RUN printf '%s\n' \
      "deb [check-valid-until=no] http://snapshot.debian.org/archive/debian/${DEBIAN_SNAPSHOT} bullseye main" \
      "deb [check-valid-until=no] http://snapshot.debian.org/archive/debian/${DEBIAN_SNAPSHOT} bullseye-updates main" \
      "deb [check-valid-until=no] http://snapshot.debian.org/archive/debian-security/${DEBIAN_SNAPSHOT} bullseye-security main" \
      > /etc/apt/sources.list \
 && rm -f /etc/apt/sources.list.d/*

RUN apt-get -o Acquire::Retries=5 update \
 && apt-get -o Acquire::Retries=5 -y install \
      git=1:2.30.2-1+deb11u5 \
      nano=5.4-2+deb11u3 \
      dos2unix=7.4.1-1 \
      build-essential=12.9 \
      ccache=4.2-1 \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /home/user

COPY framework /home/user/BugsInPy/framework
COPY projects /home/user/BugsInPy/projects
RUN chmod +x /home/user/BugsInPy/framework/bin/bugsinpy-*

ENV PATH=/home/user/BugsInPy/framework/bin:$PATH

# Image manifest: what the unpinned transitive packages actually resolved to at build time.
RUN mkdir -p /image-manifest \
 && dpkg-query -W -f='${Package}=${Version}\n' > /image-manifest/dpkg.txt \
 && conda list --explicit > /image-manifest/conda-base-explicit.txt \
 && echo "${BUGSINPY_FORK_SHA}" > /image-manifest/bugsinpy_fork_sha.txt

CMD ["/bin/bash"]
