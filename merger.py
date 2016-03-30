"""
This module contains functions for merging read count shelves into pytables wssd_out_files.
Merging can be done per contig and before all shelves are created.
"""

from __future__ import print_function
from __future__ import division

import shelve

import time
import os
import sys
import argparse
import glob

from dbm import error

import tables
from tables.exceptions import NoSuchNodeError
import numpy as np
from scipy.sparse import issparse

def convert_matrix(matrix):
    """
    Convert matrix to np.ndarray if it is sparse.
    """
    if issparse(matrix):
        matrix = matrix.toarray()
    if isinstance(matrix, np.ndarray):
        return matrix
    else:
        print("Error: unrecognized data type for matrix: %s" %
              (matrix.__class__.__name__),
              file=sys.stderr, flush=True)
        sys.exit(1)

def add_contents_to_contigs(dat, contigs):
    """Take a dictionary-like object of matrices, add matrices to contigs dictionary.
       Converts matrices to np.ndarray automatically.
    """
    for contig, matrix in dat.items():
        matrix = convert_matrix(matrix)
        if contig not in contigs:
            contigs[contig] = matrix
        else:
            contigs[contig] += matrix
    return contigs

def load_matrices_live(infiles, contigs):
    """Load infiles to contigs dictionary as they are finished."""
    fileset = set(infiles)
    total_infiles = len(fileset)
    processed_infiles = set()
    while len(fileset) > 0:
        for infile in fileset:
            # Check if infile exists and hasn't been modified in 5 minutes
            if os.path.isfile(infile) and time.time() - os.path.getmtime(infile) > 300:
                try:
                    dat = shelve.open(infile, flag="r")
                except error as err:
                    print("Error: %s: %s" % (infile, str(err)), file=sys.stderr, flush=True)
                    continue
                else:
                    contigs = add_contents_to_contigs(dat, contigs)
                    dat.close()
                    processed_infiles.add(infile)
                    print("Loaded pickle %d of %d: %s" %
                          (len(processed_infiles), total_infiles, infile),
                          file=sys.stdout, flush=True)
        fileset -= processed_infiles
        time.sleep(30)
    return contigs

def load_matrices_post(infiles, contigs):
    """Load infiles to contigs dictionary. Assumes all infiles are complete."""
    for i, infile in enumerate(infiles):
        with shelve.open(infile) as dat:
            print("Loading shelve %d of %d: %s" %
                  (i+1, len(infiles), infile),
                  file=sys.stdout, flush=True)

            contigs = add_contents_to_contigs(dat, contigs)
    return contigs

def load_matrices_per_contig_live(infiles, contig):
    """Get counts from all infiles for a given contig dictionary as they are finished.
    """
    contig_name = list(contig)[0]
    fileset = set(infiles)
    total_infiles = len(fileset)
    processed_infiles = set()
    while len(fileset) > 0:
        for infile in fileset:
            # Check if infile exists and hasn't been modified in 5 minutes
            if os.path.isfile(infile) and time.time() - os.path.getmtime(infile) > 300:
                try:
                    dat = shelve.open(infile, flag="r")
                except error as err:
                    print("Error: %s: %s" % (infile, str(err)), file=sys.stderr, flush=True)
                    continue
                else:
                    if contig_name in dat:
                        contig = add_contents_to_contigs(dat, contig)
                    dat.close()
                    processed_infiles.add(infile)
                    print("Loaded shelve %d of %d: %s" %
                          (len(processed_infiles), total_infiles, infile),
                          file=sys.stdout, flush=True)
        fileset -= processed_infiles
        time.sleep(30)
    return contig

def load_matrices_per_contig(infiles, contig):
    """Get counts from all infiles for a given contig dictionary.
    """
    contig_name = list(contig)[0]
    for i, infile in enumerate(infiles):
        with shelve.open(infile, flag="r") as dat:
            print("Contig %s: loading shelve %d of %d: %s" %
                  (contig_name, i+1, len(infiles), infile),
                  file=sys.stdout, flush=True)
            matrix = None
            if contig_name in dat:
                matrix = convert_matrix(dat["contig_name"])
        if matrix is not None:
            if contig[contig_name] is None:
                contig[contig_name] = matrix
            else:
                contig[contig_name] += matrix
    return contig

def write_to_h5(counts, fout):
    """Write counts (dictionary of contig matrices) to fout hdf5 file.
       Outfile is in wssd_out_file format.
    """
    try:
        group = fout.get_node(fout.root, "depthAndStarts_wssd")
    except NoSuchNodeError:
        group = fout.create_group(fout.root, "depthAndStarts_wssd")
    finally:
        for i, (contig, matrix) in enumerate(counts.items()):
            print("Merger: %d Creating array for %s" %(i+1, contig), file=sys.stdout, flush=True)
            nrows, ncols = matrix.shape
            nedists = nrows // 2
            wssd_contig = matrix.T

            carray_empty = tables.CArray(group,
                                         contig,
                                         tables.UInt32Atom(),
                                         (ncols, nedists, 2),
                                         filters=tables.Filters(complevel=1, complib="lzo")
                                        )

            # Add depth counts
            carray_empty[:, :, 0] = wssd_contig[:, nedists:]

            # Add starts
            carray_empty[:, :, 1] = wssd_contig[:, 0:nedists]

            fout.flush()

def write_wssd_to_h5(wssd, fout):
    """Append single contig wssd_out_file to fout hdf5 file.
       Outfile is in wssd_out_file format.
    """
    try:
        group = fout.get_node(fout.root, "depthAndStarts_wssd")
    except NoSuchNodeError:
        group = fout.create_group(fout.root, "depthAndStarts_wssd")
    finally:
        matrix = wssd.list_nodes("/depthAndStarts_wssd")[0]
        first, second, third = matrix.shape

        carray_empty = tables.CArray(group,
                                     matrix.name,
                                     tables.UInt32Atom(),
                                     (first, second, third),
                                     filters=tables.Filters(complevel=1, complib="lzo")
                                    )
        carray_empty = matrix
        fout.flush()

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("outfile", help="Path to output wssd_out_file")
    parser.add_argument("--infiles", nargs="+", default=None, help="List of input shelves to merge")
    parser.add_argument("--infile_glob", default=None, help="glob string for infiles")
    parser.add_argument("--live_merge",
                        action="store_true",
                        help="Start merging infiles before they are all finished? \
                              (Default: %(default)s)"
                       )
    parser.add_argument("--contigs_file",
                        default=None,
                        help="Tab-delimited table with contig names in the first column")
    parser.add_argument("--contig", default=None, help="Name of contig to merge")
    parser.add_argument("--per_contig_merge",
                        action="store_true",
                        help="Merge matrices one contig at a time (low memory footprint)"
                       )
    parser.add_argument("--wssd_merge",
                        nargs="+",
                        default=None,
                        help="Merge multiple wssd_out_files"
                       )

    args = parser.parse_args()

    if args.per_contig_merge:
        if args.contigs_file is None and args.contig is None:
            print("Must specify --contigs_file or --contig for per_contig_merge", file=sys.stderr)
            sys.exit(1)

    start_time = time.time()

    #fout = tables.open_file(args.outfile, mode="w")
    #print("Successfully opened outfile: %s" % args.outfile, file=sys.stdout, flush=True)

    contig_dict = {}
    contig_list = []

    if args.per_contig_merge:
        if args.contig is not None:
            contig_list.append(args.contig)
        if args.contig_file is not None:
            with open(args.contigs_file, "r") as contigs_file:
                for line in contigs_file:
                    contig_name = line.rstrip().split()[0]
                    contig_list.append(contig_name)

    infiles = []

    if args.infile_glob is not None:
        infiles.extend(glob.glob(args.infile_glob))

    if args.infiles is not None:
        infiles.extend(args.infiles)

    # Remove extensions and get unique shelves
    if infiles != []:
        infiles = [x.replace(".dat", "").replace(".bak", "").replace(".dir", "") for x in infiles]
        infiles = list(set(infiles))

    if args.per_contig_merge:
        for contig_name in contig_list:
            contig_dict = {}
            contig_dict[contig_name] = None
            contig_dict = load_matrices_per_contig(infiles, contig_dict)
            with tables.open_file(args.outfile, mode="a") as fout:
                print("Successfully opened outfile: %s" % args.outfile, file=sys.stdout, flush=True)
                write_to_h5(contig_dict, fout)

    elif args.wssd_merge is None:
        if args.live_merge:
            contig_dict = load_matrices_live(infiles, contig_dict)
        else:
            contig_dict = load_matrices_post(infiles, contig_dict)
        print("Finished loading shelves. Creating h5 file: %s" % args.outfile,
              file=sys.stdout, flush=True)
        with tables.open_file(args.outfile, mode="a") as fout:
            print("Successfully opened outfile: %s" % args.outfile, file=sys.stdout, flush=True)
            write_to_h5(contig_dict, fout)

    else:
        # Merge wssd files into single wssd_out_file
        with tables.open_file(args.outfile, mode="a") as fout:
            print("Successfully opened outfile: %s" % args.outfile, file=sys.stdout, flush=True)
            for wssd_file in args.wssd_merge:
                print("Reading wssd_file: %s" % wssd_file, file=sys.stdout, flush=True)
                with tables.open_file(wssd_file, mode="r") as wssd:
                    write_wssd_to_h5(wssd, fout)

    finish_time = time.time()
    print("Finished writing wssd_out_file in %d seconds. Closing." %
          (finish_time - start_time),
          file=sys.stdout,
          flush=True
         )
