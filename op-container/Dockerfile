FROM ubuntu:jammy-20230816

ENV DEBIAN_FRONTEND=noninteractive
ENV QT_QPA_PLATFORM=offscreen
ARG PYTHON_VERSION=3.9.17
ARG OPENPYPE_VERSION=3.16
ARG CW_GROUP_CI_TOKEN

# Install dependencies
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        curl \
        git \
        file \
        dnsutils \
        ca-certificates \
        build-essential \
        libssl-dev \
        libffi-dev \
        zlib1g-dev \
        libncurses5-dev \
        libncursesw5-dev \
        libreadline-dev \
        libsqlite3-dev \
        libgdbm-dev \
        libdb5.3-dev \
        libbz2-dev \
        libexpat1-dev \
        liblzma-dev \
        libgl1-mesa-glx \
        tk-dev

# Install pyenv
RUN git clone https://github.com/pyenv/pyenv.git ~/.pyenv
ENV PYENV_ROOT /root/.pyenv
ENV PATH $PYENV_ROOT/shims:$PYENV_ROOT/bin:$PATH

# Install Python 3.9.16 and set it as default
RUN pyenv install $PYTHON_VERSION && pyenv global $PYTHON_VERSION

COPY ./init.sh /init
RUN chmod +x /init

# Install OpenPype
RUN curl "https://bin:${CW_GROUP_CI_TOKEN}@gitlab.alkemy-x.com/api/v4/projects/58/packages/generic/openpype/$OPENPYPE_VERSION/openpype-$OPENPYPE_VERSION-ubuntu22.04.tar.gz" --output openpype-exe.linux-x86_64-3.9.tar.gz \
    && tar -xvzf openpype-exe.linux-x86_64-3.9.tar.gz \
    && rm openpype-exe.linux-x86_64-3.9.tar.gz  \
    && mkdir -p /usr/openpype  \
    && mv ./build/exe.linux-x86_64-3.9/ /usr/openpype/$OPENPYPE_VERSION

WORKDIR /usr/openpype/$OPENPYPE_VERSION

ENTRYPOINT ["/init"]
