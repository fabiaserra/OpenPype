FROM aswf/ci-ocio:2022

ENV DEBIAN_FRONTEND=noninteractive
ENV QT_QPA_PLATFORM=offscreen
ARG PYTHON_VERSION=3.9.17
ARG OPENPYPE_VERSION=3.16
ARG CW_GROUP_CI_TOKEN

# Install dependencies
RUN yum install -y \
    qt5-qtbase-devel \
    numactl-libs \
    file \
    git \
    net-tools \
    which && \
    yum clean all

RUN pip install keyring

# Install pyenv
RUN git clone https://github.com/pyenv/pyenv.git ~/.pyenv
ENV PYENV_ROOT /root/.pyenv
ENV PATH $PYENV_ROOT/shims:$PYENV_ROOT/bin:$PATH

# Install Python and set it as default
RUN pyenv install $PYTHON_VERSION && pyenv global $PYTHON_VERSION

# Install OpenPype
RUN curl "https://bin:${CW_GROUP_CI_TOKEN}@gitlab.alkemy-x.com/api/v4/projects/58/packages/generic/openpype/$OPENPYPE_VERSION/openpype-$OPENPYPE_VERSION-ubuntu22.04.tar.gz" --output openpype-exe.linux-x86_64-3.9.tar.gz \
    && tar -xvzf openpype-exe.linux-x86_64-3.9.tar.gz \
    && mkdir -p /opt/openpype/ \
    && mv ./build/exe.linux-x86_64-3.9/ /opt/openpype/${OPENPYPE_VERSION} \
    && rm openpype-exe.linux-x86_64-3.9.tar.gz

ENV PATH="${PATH}:/opt/openpype/${OPENPYPE_VERSION}"

WORKDIR /opt/openpype/${OPENPYPE_VERSION}

# Copy certificates
COPY ca-certificates.crt /etc/ssl/certs/ca-certificates.crt

# Set up Init & Permissions
COPY init.sh /init
RUN chmod +x /init && \
    echo "umask 000" >> /etc/profile && \
    echo "umask 000" >> /etc/bashrc

ENTRYPOINT [ "/init" ]
