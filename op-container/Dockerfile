FROM ubuntu:jammy-20230816

# Add Ayon and OP binaries to PATH so it's easier to launch
ENV PATH="${PATH}:/pipe/ayon/release/current"
ENV PATH="${PATH}:/pipe/openpype/release/current"

# Install dependencies
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        zip \
        unzip \
        pigz \
        file \
        curl \
        git \
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

# Copy certificates
COPY ca-certificates.crt /etc/ssl/certs/ca-certificates.crt

# Set up Init & Permissions
COPY init.sh /init
RUN chmod +x /init && \
    echo "umask 000" >> /etc/profile && \
    echo "umask 000" >> /etc/bashrc

ENTRYPOINT [ "/init" ]
