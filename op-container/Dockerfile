FROM ubuntu:jammy-20230816

# Add Ayon and OP binaries to PATH so it's easier to launch
ENV PATH="${PATH}:/pipe/ayon/release/current"
ENV PATH="${PATH}:/pipe/openpype/release/current"

# Install dependencies
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        zip \
        file

# Copy certificates
COPY ca-certificates.crt /etc/ssl/certs/ca-certificates.crt

# Set up Init & Permissions
COPY init.sh /init
RUN chmod +x /init && \
    echo "umask 000" >> /etc/profile && \
    echo "umask 000" >> /etc/bashrc

ENTRYPOINT [ "/init" ]
