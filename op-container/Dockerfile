FROM aswf/ci-ocio:2024

# Add Ayon and OP binaries to PATH so it's easier to launch
ENV PATH="${PATH}:/pipe/ayon/release/current"
ENV PATH="${PATH}:/pipe/openpype/release/current"

# Set up Init & Permissions
COPY init.sh /init
RUN chmod +x /init && \
    echo "umask 000" >> /etc/profile && \
    echo "umask 000" >> /etc/bashrc

ENTRYPOINT [ "/init" ]
