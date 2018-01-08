# -----------------------------------------------------------------------------
# Copyright (c) 2014--, The Qiita Development Team.
#
# Distributed under the terms of the BSD 3-clause License.
#
# The full license is in the file LICENSE, distributed with this software.
# -----------------------------------------------------------------------------

from itertools import zip_longest
import os
from os.path import basename, join, exists
from functools import partial
from tempfile import mkdtemp
from shutil import rmtree

from qiita_client import ArtifactInfo

from qiita_client.util import system_call, get_sample_names_by_run_prefix

BOWTIE2_PARAMS = {
    'x': 'Bowtie2 database to filter',
    'p': 'Number of threads to be used'}

DATABASE_PREFIX = {
    'Human': 'Human/phix'}


def make_read_pairs_per_sample(forward_seqs, reverse_seqs, map_file):
    """Recovers read pairing information

    Parameters
    ----------
    forward_seqs : list of str
        The list of forward seqs filepaths
    reverse_seqs : list of str
        The list of reverse seqs filepaths
    map_file : str
        The path to the mapping file

    Returns
    -------
    samples: list of tup
        list of 4-tuples with run prefix, sample name, fwd read fp, rev read fp

    Raises
    ------
    ValueError
        If the rev is not an empty list and the same length than fwd seqs
        The prefixes of the run_prefix don't match the file names

    Notes
    -----
    At this stage it is required that if reverse sequences are present that all
    samples have both a forward and a reverse sequence.
    """

    # sort forward seqs
    forward_seqs.sort()

    # check that rev seqs are same len
    if reverse_seqs:
        if len(forward_seqs) != len(reverse_seqs):
            raise ValueError('Your reverse and forward files are of different '
                             'length. Forward: %s. Reverse: %s.' %
                             (', '.join(forward_seqs),
                              ', '.join(reverse_seqs)))
        reverse_seqs.sort()

    # get run prefixes
    # These are prefixes that should match uniquely to forward reads
    # sn_by_rp is dict of samples keyed by run prefixes
    sn_by_rp = get_sample_names_by_run_prefix(map_file)

    # make pairings
    samples = []
    used_prefixes = set()
    for i, (fwd_fp, rev_fp) in enumerate(zip_longest(forward_seqs,
                                                     reverse_seqs)):
        # fwd_fp is the fwd read filepath
        fwd_fn = basename(fwd_fp)

        # iterate over run prefixes and make sure only one matches
        run_prefix = None
        for rp in sn_by_rp:
            if fwd_fn.startswith(rp) and run_prefix is None:
                run_prefix = rp
            elif fwd_fn.startswith(rp) and run_prefix is not None:
                raise ValueError('Multiple run prefixes match this fwd read: '
                                 '%s' % fwd_fn)

        # make sure that we got one matching run prefix:
        if run_prefix is None:
            raise ValueError('No run prefix matching this fwd read: %s'
                             % fwd_fn)

        if run_prefix in used_prefixes:
            raise ValueError('This run prefix matches multiple fwd reads: '
                             '%s' % run_prefix)

        if rev_fp is None:
            samples.append((run_prefix, sn_by_rp[run_prefix], fwd_fp, None))
        else:
            rev_fn = basename(rev_fp)
            # if we have reverse reads, make sure the matching pair also
            # matches the run prefix:
            if not rev_fn.startswith(run_prefix):
                raise ValueError('Reverse read does not match this run prefix.'
                                 '\nRun prefix: %s\nForward read: %s\n'
                                 'Reverse read: %s\n' %
                                 (run_prefix, fwd_fn, rev_fn))

            samples.append((run_prefix, sn_by_rp[run_prefix], fwd_fp,
                            rev_fp))

        used_prefixes.add(run_prefix)

    return(samples)


def _format_qc_filter_params(parameters):
    params = []
    # Loop through all of the commands alphabetically
    for param in sorted(BOWTIE2_PARAMS):
        # Find the value using long parameter names
        parameter = BOWTIE2_PARAMS[param]
        value = parameters[parameter]
        dash = '--'
        # Check for single letter commands
        if len(param) == 1:
            dash = '-'
        if value is 'True':
            params.append('%s%s' % (dash, param))
        elif value is 'False':
            continue
        elif value and value != 'default':
            # Check for database option
            if param is 'x':
                db_path = os.environ["QC_FILTER_DB_DP"]
                value = join(db_path, DATABASE_PREFIX[value])
            params.append('%s%s %s' % (dash, param, value))

    param_string = ' '.join(params)

    return(param_string)


def generate_qc_filter_commands(forward_seqs, reverse_seqs, map_file,
                                out_dir, temp_dir, parameters):
    """Generates the QC_Filter commands

    Parameters
    ----------
    forward_seqs : list of str
        The list of forward seqs filepaths
    reverse_seqs : list of str
        The list of reverse seqs filepaths
    map_file : str
        The path to the mapping file
    out_dir : str
        The job output directory
    parameters : dict
        The command's parameters, keyed by parameter name

    Returns
    -------
    cmds: list of str
        The QC_Filter commands
    samples: list of tup
        list of 4-tuples with run prefix, sample name, fwd read fp, rev read fp

    Notes
    -----
    Currently this is requiring matched pairs in the make_read_pairs_per_sample
    step but implicitly allowing empty reverse reads in the actual command
    generation. This behavior may allow support of situations with empty
    reverse reads in some samples, for example after trimming and QC.
    """
    # we match filenames, samples, and run prefixes
    samples = make_read_pairs_per_sample(forward_seqs, reverse_seqs, map_file)

    cmds = []

    param_string = _format_qc_filter_params(parameters)
    threads = parameters['Number of threads to be used']

    for run_prefix, sample, f_fp, r_fp in samples:
        cmds.append('bowtie2 %s --very-sensitive -1 %s -2 %s | '
                    'samtools view -f 12 -F 256 -b -o %s;'

                    'samtools sort -T %s -@ %s -n -o %s %s;'

                    'bedtools bamtofastq -i %s -fq %s -fq2 %s;'

                    'pigz -p %s -c %s > %s;'
                    'pigz -p %s -c %s > %s'

                    % (param_string, f_fp, r_fp,
                       join(temp_dir, '%s.unsorted.bam' % sample),
                       join(temp_dir, '%s' % sample), threads,
                       join(temp_dir, '%s.bam' % sample),
                       join(temp_dir, '%s.unsorted.bam' % sample),
                       join(temp_dir, '%s.bam' % sample),
                       join(temp_dir, '%s.R1.trimmed.filtered.fastq' % sample),
                       join(temp_dir, '%s.R2.trimmed.filtered.fastq' % sample),
                       threads,
                       join(temp_dir, '%s.R1.trimmed.filtered.fastq'
                            % sample),
                       join(out_dir, '%s.R1.trimmed.filtered.fastq.gz'
                            % sample),
                       threads,
                       join(temp_dir, '%s.R2.trimmed.filtered.fastq' % sample),
                       join(out_dir, '%s.R2.trimmed.filtered.fastq.gz'
                            % sample)
                       ))

    return cmds, samples


def _run_commands(qclient, job_id, commands, msg):
    for i, cmd in enumerate(commands):
        qclient.update_job_step(job_id, msg % i)
        std_out, std_err, return_value = system_call(cmd)
        if return_value != 0:
            error_msg = ("Error running QC_Filter:\nStd out: %s\nStd err: %s"
                         "\n\nCommand run was:\n%s"
                         % (std_out, std_err, cmd))
            return False, error_msg

    return True, ""


def _per_sample_ainfo(out_dir, samples, fwd_and_rev=False):
    files = []
    missing_files = []

    suffixes = ['%s.R1.trimmed.filtered.fastq.gz',
                '%s.R2.trimmed.filtered.fastq.gz']

    for _, rp, _, _ in samples:
        smd = partial(join, out_dir)
        for suff in suffixes:
            fname = smd(suff % rp)
            if exists(fname):
                files.append(fname)
            else:
                missing_files.append(fname)

    if not files:
        # QC_Filter did not create any files, which means that no sequence
        # was kept after filteration
        raise ValueError("No sequences left after filtering")

    return [ArtifactInfo('QC_Trim files', 'per_sample_FASTQ', files)]


def qc_filter(qclient, job_id, parameters, out_dir):
    """Run filtering using Bowtie2 with the given parameters

    Parameters
    ----------
    qclient : tgp.qiita_client.QiitaClient
        The Qiita server client
    job_id : str
        The job id
    parameters : dict
        The parameter values to run split libraries
    out_dir : str
        The path to the job's output directory

    Returns
    -------
    bool, list, str
        The results of the job
    """
    # Step 1 get the rest of the information need to run Bowtie2
    qclient.update_job_step(job_id, "Step 1 of 4: Collecting information")
    artifact_id = parameters['input']
    del parameters['input']

    # Get the artifact filepath information
    artifact_info = qclient.get("/qiita_db/artifacts/%s/" % artifact_id)
    fps = artifact_info['files']

    # Get the artifact metadata
    prep_info = qclient.get('/qiita_db/prep_template/%s/'
                            % artifact_info['prep_information'][0])
    qiime_map = prep_info['qiime-map']

    # Step 2 generating command
    qclient.update_job_step(job_id, "Step 2 of 4: Generating"
                                    " QC_Filter commands")
    # Creating temp directory for intermediate files
    temp_dir = mkdtemp()

    rs = fps['raw_reverse_seqs'] if 'raw_reverse_seqs' in fps else []
    commands, samples = generate_qc_filter_commands(fps['raw_forward_seqs'],
                                                    rs, qiime_map, out_dir,
                                                    temp_dir, parameters)

    # Step 3 execute filtering command
    len_cmd = len(commands)
    msg = "Step 3 of 4: Executing QC_Trim job (%d/{0})".format(len_cmd)
    success, msg = _run_commands(qclient, job_id, commands, msg)
    if not success:
        return False, None, msg

    # Step 4 generating artifacts
    msg = "Step 4 of 4: Generating new artifacts (%d/{0})".format(len_cmd)
    success, msg = _run_commands(qclient, job_id, commands, msg)
    ainfo = _per_sample_ainfo(out_dir, samples, bool(rs))

    rmtree(temp_dir)

    return True, ainfo, ""