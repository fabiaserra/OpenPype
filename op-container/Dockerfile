FROM aswf/ci-ocio:2023

# Add OP binary to PATH so it's easier to launch
ENV PATH="${PATH}:/pipe/openpype/release/current"

# Set up Init & Permissions
COPY init.sh /init
RUN chmod +x /init && \
    echo "umask 000" >> /etc/profile && \
    echo "umask 000" >> /etc/bashrc

ENTRYPOINT [ "/init" ]
