from pathlib import Path
from typing import Dict, List, Optional
from k8spurifier.analysis import AnalysisResult
from k8spurifier.scheduler import AnalysisScheduler
from k8spurifier.applicationobject import ApplicationObject
from k8spurifier.frontend.config import ApplicationConfig
from k8spurifier.frontend.parsers.docker import DockerfileParser
from k8spurifier.frontend.parsers.kubernetes import KubernetesConfigParser
from loguru import logger
from k8spurifier.frontend.parsers.openapi import OpenAPIParser
from k8spurifier.service import Service
from kubernetes import config


class Application:
    def __init__(self, context_path: Path) -> None:
        self.context_path = context_path
        self.application_objects: List[ApplicationObject] = []
        self.services: Dict[str, Service] = {}
        self.analysis_results: List[AnalysisResult] = []

        # flags to run analyses types
        self.run_static = True
        self.run_dynamic = True

    def set_config_path(self, config_path) -> None:
        self.config_path = config_path

    def set_context_path(self, context_path):
        self.context_path = context_path

    def aquire_application(self):
        self.config = ApplicationConfig(self.context_path)
        self.config.load_config_from_file(self.config_path)
        self.repositories = self.config.acquire_application()

    def parse_application(self):
        logger.info('parsing the application...')
        application_objects = []

        deployment = self.config.deployment()

        # parse the services
        config_services = self.config.services()

        # parse all services names
        for service_data in config_services:
            service_name = service_data['name']
            service = Service(service_name)
            self.services[service_name] = service

        # parse the service properties
        for service, properties in self.config.properties():
            self.services[service].properties = properties

        # parse kubernetes config files
        if 'kubernetes' in deployment:
            kubernetes_config = deployment['kubernetes']
            repository = self.repositories[kubernetes_config['repository']]
            files = repository.get_artifacts_by_regex(
                kubernetes_config['glob'])
            for f in files:
                kubernetes_parser = KubernetesConfigParser(
                    repository, f, self.services)
                kubernetes_objects = kubernetes_parser.parse()

                # append all the objects in the application objects list
                for obj in kubernetes_objects:
                    application_objects.append(obj)

        # parse services' dockerfiles and openapis
        dockerfiles: List[ApplicationObject] = []
        openapis: List[ApplicationObject] = []
        for service_data in config_services:
            # TODO validation
            service_repository = self.repositories[service_data['repository']]
            service = self.services[service_data['name']]

            if 'dockerfile' in service_data:
                # get the image name if it exists
                image_name = ''
                if 'image' in service_data:
                    image_name = service_data['image']

                # parse the dockerfile
                dockerfile_parser = DockerfileParser(
                    service_repository, service_data['dockerfile'], image_name=image_name)
                dockerfile_objects = dockerfile_parser.parse()
                for obj in dockerfile_objects:
                    if service.properties is not None:
                        obj.service_properties = dict(service.properties)

                    dockerfiles.append(obj)

                if len(dockerfile_objects) > 0:
                    service.dockerfile = dockerfile_objects[0]

            if 'openapi' in service_data:
                openapi_path = service_data['openapi']
                openapi_parser = OpenAPIParser(
                    service_repository, openapi_path)
                openapi_objects = openapi_parser.parse()

                for obj in openapi_objects:
                    if service.properties is not None:
                        obj.service_properties = dict(service.properties)

                    openapis.append(obj)

                if len(openapi_objects) > 0:
                    service.dockerfile = openapi_objects[0]

        # deduplication of dockerfiles
        seen_openapis = set()
        for spec in dockerfiles:
            if spec.path not in seen_openapis:
                seen_openapis.add(spec.path)
                application_objects.append(spec)

        # deduplication of openapi specifications
        seen_openapis = set()
        for spec in openapis:
            if spec.path not in seen_openapis:
                seen_openapis.add(spec.path)
                application_objects.append(spec)

        for obj in application_objects:
            logger.debug(obj)

        self.application_objects = application_objects

        logger.info(
            f'finished parsing: {len(application_objects)} resulting objects')

    def get_service(self, service_name: str) -> Optional[Service]:
        if service_name in self.services:
            return self.services[service_name]
        return None

    def load_kubernetes_cluster_config(self):
        # TODO add configuration path setting
        logger.info('Trying to load the kubernetes cluster config')
        try:
            config.load_kube_config()
        except Exception:
            logger.warning(
                'failed to load kubernetes config, ignoring dynamic analyses')
            self.run_dynamic = False
        logger.info('Successfully loaded kubernetes cluster config')

    def run_analyses(self):
        logger.info('running analyses on the application')
        scheduler = AnalysisScheduler(self.application_objects)
        self.analysis_results = scheduler.run_analyses(
            self.run_static, self.run_dynamic)

    def show_results(self):
        print('Analysis results:')
        for result in self.analysis_results:
            description_formatted = '\t' + \
                '\n\t'.join(result.description.split('\n'))
            print(f"{result.generating_analysis} - detected smells {result.smells_detected}\n"
                  f"{description_formatted}")
