import logging
import tempfile
import threading

import os
import copy
import uuid
import datetime
import time
from abc import ABCMeta, abstractmethod
import prov.model as prov
from typing import Dict, Text, Any, Tuple, Set, List
from schema_salad.validate import ValidationException

from .builder import Builder
from .errors import WorkflowException
from .mutation import MutationManager
from .job import JobBase
from .process import relocateOutputs, cleanIntermediate, Process, shortname, uniquename, get_overrides
from . import loghandler
from schema_salad.sourceline import SourceLine


_logger = logging.getLogger("cwltool")

class JobExecutor(object):
    __metaclass__ = ABCMeta

    def __init__(self):
        # type: (...) -> None
        self.final_output = []  # type: List
        self.final_status = []  # type: List
        self.output_dirs = set()  # type: Set

    def __call__(self, *args, **kwargs):
        return self.execute(*args, **kwargs)

    def output_callback(self, out, processStatus):
        self.final_status.append(processStatus)
        self.final_output.append(out)

    @abstractmethod
    def run_jobs(self,
                 t,  # type: Process
                 job_order_object,  # type: Dict[Text, Any]
                 provobj,
                 logger,
                 make_fs_access,
                 **kwargs  # type: Any
                 ):
        pass

    def execute(self, t,  # type: Process
                job_order_object,  # type: Dict[Text, Any]
                provobj,
                logger=None,
                makeTool=None,
                select_resources=None,
                make_fs_access=None,
                secret_store=None,
                **kwargs  # type: Any
                ):
        # type: (...) -> Tuple[Dict[Text, Any], Text]

        if "basedir" not in kwargs:
            raise WorkflowException("Must provide 'basedir' in kwargs")
        finaloutdir = os.path.abspath(kwargs.get("outdir")) if kwargs.get("outdir") else None
        kwargs["outdir"] = tempfile.mkdtemp(prefix=kwargs["tmp_outdir_prefix"]) if kwargs.get(
            "tmp_outdir_prefix") else tempfile.mkdtemp()
        self.output_dirs.add(kwargs["outdir"])
        kwargs["mutation_manager"] = MutationManager()
        kwargs["toplevel"] = True

        jobReqs = None
        if "cwl:requirements" in job_order_object:
            jobReqs = job_order_object["cwl:requirements"]
        elif ("cwl:defaults" in t.metadata and "cwl:requirements" in t.metadata["cwl:defaults"]):
            jobReqs = t.metadata["cwl:defaults"]["cwl:requirements"]
        if jobReqs:
            for req in jobReqs:
                t.requirements.append(req)
        self.run_jobs(t, job_order_object, provobj, logger, make_fs_access, **kwargs)

        if self.final_output and self.final_output[0] and finaloutdir:
            self.final_output[0] = relocateOutputs(
                self.final_output[0], finaloutdir, self.output_dirs,
                kwargs.get("move_outputs"), make_fs_access(""),
                kwargs.get("compute_checksum", True))

        if kwargs.get("rm_tmpdir"):
            cleanIntermediate(self.output_dirs)

        if self.final_output and self.final_status:
            return (self.final_output[0], self.final_status[0])
        else:
            return (None, "permanentFail")


class SingleJobExecutor(JobExecutor):
    def run_jobs(self,
                 t,  # type: Process
                 job_order_object=None,  # type: Dict[Text, Any]
                 provobj=None,
                 logger=None,
                 make_fs_access=None,
                  **kwargs   
                 ):

        reference_locations={} # type: Dict[Text, Any]
        ProcessProvActivity=None
        jobiter = t.job(job_order_object,
                        self.output_callback,
                        **kwargs)
        try:
            research_obj = kwargs.get("research_obj")
            for r in jobiter:
                if r:
                    builder = kwargs.get("builder", None)  # type: Builder

                    if builder is not None:
                        r.builder = builder
                    if r.outdir:
                        self.output_dirs.add(r.outdir)
                    if research_obj:
                        if not hasattr(t, "steps"): #record provenance of an independent commandline tool execution
                            provobj.prospective_prov(r)
                            customised_job=research_obj.copy_job_order(r, job_order_object)
                            relativised_input_object, reference_locations =research_obj.create_job(customised_job, make_fs_access, kwargs) 
                            provobj.declare_artefact(relativised_input_object, job_order_object)
                            ProcessProvActivity = provobj.startProcess(r, provobj.document, provobj.engineUUID)
                        elif hasattr(r, "workflow"): #record provenance for the workflow execution
                            provobj.prospective_prov(r)
                            customised_job=research_obj.copy_job_order(r, job_order_object)
                            relativised_input_object, reference_locations =research_obj.create_job(customised_job, make_fs_access, kwargs) 
                            provobj.declare_artefact(relativised_input_object, job_order_object)
                        else: #in case of commandline tool execution as part of workflow
                            ProcessProvActivity = provobj.startProcess(r, provobj.document, provobj.engineUUID, provobj.workflowRunURI)
                        r.run(provobj, ProcessProvActivity, reference_locations, **kwargs)
                        #capture workflow level outputs in the prov doc
                        if self.final_output:
                            ProcessRunID=None
                            provobj.generate_outputProv(self.final_output[0], ProcessRunID)
                    else:
                        r.run(**kwargs)
                else:
                    logger.error("Workflow cannot make any more progress.")
                    break
        except (ValidationException, WorkflowException):
            raise
        except Exception as e:
            logger.exception("Got workflow error")
            raise WorkflowException(Text(e))


class MultithreadedJobExecutor(JobExecutor):
    def __init__(self):
        super(MultithreadedJobExecutor, self).__init__()
        self.threads = set()
        self.exceptions = []

    def run_job(self,
                job,      # type: JobBase
                **kwargs  # type: Any
                ):
        # type: (...) -> None
        def runner():
            try:
                job.run(**kwargs)
            except WorkflowException as e:
                self.exceptions.append(e)
            except Exception as e:
                self.exceptions.append(WorkflowException(Text(e)))

            self.threads.remove(thread)

        thread = threading.Thread(target=runner)
        thread.daemon = True
        self.threads.add(thread)
        thread.start()

    def wait_for_next_completion(self):  # type: () -> None
        if self.exceptions:
            raise self.exceptions[0]

    def run_jobs(self,
                 t,  # type: Process
                 job_order_object,  # type: Dict[Text, Any]
                 provobj,
                 logger,
                 make_fs_access,
                 **kwargs  # type: Any
                 ):

        jobiter = t.job(job_order_object, self.output_callback, **kwargs)

        for r in jobiter:
            if r:
                builder = kwargs.get("builder", None)  # type: Builder
                if builder is not None:
                    r.builder = builder
                if r.outdir:
                    self.output_dirs.add(r.outdir)
                self.run_job(r, **kwargs)
            else:
                if len(self.threads):
                    self.wait_for_next_completion()
                else:
                    logger.error("Workflow cannot make any more progress.")
                    break

        while len(self.threads) > 0:
            self.wait_for_next_completion()