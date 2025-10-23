FROM minio/minio:latest

COPY init /usr/local/bin/init
RUN chmod +x /usr/local/bin/init/*.sh

ENTRYPOINT ["/usr/local/bin/init/create-buckets.sh"]
CMD ["server", "--console-address", ":9000", "/data"]
