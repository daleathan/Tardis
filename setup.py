#! /usr/bin/python

from setuptools import setup, find_packages
import sys, os

version = '0.1'

setup(  name                    = 'Tardis',
        version                 = version,
        description             = "Demo Pakcage",
        packages                = find_packages(exclude=['ez_setup', 'examples', 'tests']),
        include_package_data    = True,
        zip_safe                = False,
        install_requires = ['bson', 'pycrypto', 'parsedatetime' ],
        data_files = [( 'etc/tardis', ['tardisd.cfg'] ),
                      ( 'schema', ['schema/tardis.sql']) ],
        entry_points = {
                'console_scripts' : [
                    'tardis = Tardis.TardisClient:main',
                    'tardisd = Tardis.TardisDaemon:main',
                    'tardisfs = Tardis.TardisFS:main',
                    'regenerate = Tardis.Regenerate:main',
                ],
        },
    )
