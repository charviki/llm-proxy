import logging

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s - %(message)s',
    force=True
)

logger = logging.getLogger("test_docker")
logger.info("This is a test log in docker")
print("This is a standard print")
