"""Module providing a function for main function """

# pylint: disable=C0116

import logging
import logging.config
from datetime import datetime

def setup_logging(log_level=logging.INFO):
    # Generate log file name with the current date
    log_file_name = f"/tmp/auto_trade_{datetime.now().strftime('%Y-%m-%d')}.log"
    
    logging_config = {
        'version': 1,
        'disable_existing_loggers': False,
        'formatters': {
            'standard': {
                'format': '%(asctime)s [%(levelname)s] (%(filename)s:%(lineno)d): %(message)s'
            },
        },
        'handlers': {
            'file': {
                'level': log_level,
                'class': 'logging.FileHandler',
                'formatter': 'standard',
                'filename': log_file_name,  # Log file path with date
            },
        },
        'loggers': {
            '': {
                'handlers': ['file'],
                'level': log_level,
                'propagate': True
            },
        }
    }

    logging.config.dictConfig(logging_config)

setup_logging()
