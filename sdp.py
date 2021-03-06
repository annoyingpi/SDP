from __future__ import division, print_function
import cvxpy as cvx
import numpy
import scipy.linalg as linalg
import random
import subprocess
import os
from opt_utils import rand_matrix


class SDP:
    tmpdir = "tmp"
    def __init__(self, A=None, B=None, C=None, D=None):
        """Generate internal variables.

        The spectrahedron is the surface det(xA + yB + zC + D) = 0.
        mins holds the minimizing points of randomly-generated SDPs.
        pmins is an array indicating points with multiplicities.
        Each element of pmins takes the form (min, occurances)

        """
        self.mins = []
        self.pmins = []
        self.nodes = []
        # location, eigenvalue pairs
        self.spec_nodes = []
        self.sym_nodes = []
        # number of cvx calls
        self.trials = 0
        # track whether the spectrahedron contains an NSD component,
        # and whether how many optimization directions are
        # simultaneously unbounded for both (or neither) components
        self.psd_spec = True
        self.nsd_spec = True
        self.fully_bounded_directions = 0
        self.fully_unbounded_directions = 0

        if A is None:
            self.A = rand_matrix(5,5,symmetric=True,integer=True)
        else:
            self.A = A

        if B is None:
            self.B = rand_matrix(5,5,symmetric=True,integer=True)
        else:
            self.B = B

        if C is None:
            self.C = rand_matrix(5,5,symmetric=True,integer=True)
        else:
            self.C = C

        if D is None:
            self.D = numpy.identity(5, dtype=int)
        else:
            self.D = D

        # list of matrices for convenience
        self.matrices = [self.A, self.B, self.C, self.D]


    #
    # Utility functions
    #
    def matrix(self, vector):
        """Return (xA+yB+zC+D) at a point."""
        vec = vector[:]
        vec.append(1)
        return sum([vec[i] * self.matrices[i]
                                     for i in range(len(self.matrices))])


    def eigenvalues(self, vector):
        """Return the eigenvalues of (xA+yB+zC+D) at a point."""
        svd = linalg.svd(self.matrix(vector))
        eivals = svd[1]
        for i in range(len(eivals)):
            if svd[0][i,i] * svd[2][i,i] < 0:
                eivals[i] *= -1
        return eivals


    #
    # functions for singular handler
    #
    def get_nodes_from_singular(self):
        """Determine location of nodes with singular."""
        tmpfile = self.tmpdir + '/' + str(random.randrange(2 ** 32))
        with open(tmpfile,'w') as f:
            self.print_singular_script(file=f)
        output = subprocess.check_output(['singular',tmpfile])
        os.remove(tmpfile)
        return self.parse_singular_output(output)


    def matrix_to_singular(self, matrix):
        """Format a matrix for input into singular."""
        return str([i for i in matrix.flat])[1:-1]


    def print_singular_script(self, template="data/singular_script",
                              file=None):
        with open(template) as f:
            for line in f.readlines():
                print(line.format(A=self.matrix_to_singular(self.A),
                                  B=self.matrix_to_singular(self.B),
                                  C=self.matrix_to_singular(self.C),
                                  D=self.matrix_to_singular(self.D)),
                      end='',file=file)


    def parse_singular_output(self, string):
        """Parse the output from singular and return list of nodes."""
        split = string[string.find('[1]'):].splitlines()
        vectors = []
        for i in range(0,140,7):
            if '(' in split[i+2] or '(' in split[i+4] or '(' in split[i+6]:
                continue
            vectors.append([float(split[i+j]) for j in range(2,8,2)])
        return vectors


    #
    # main components
    #
    def print_params(self, file=None):
        """print the matrix parameters to a file or stdout"""
        print('A:', file=file)
        print(self.A, file=file)
        print([a for a in self.A.flat], file=file)
        print('B:', file=file)
        print(self.B, file=file)
        print([b for b in self.B.flat], file=file)
        print('C:', file=file)
        print(self.C, file=file)
        print([c for c in self.C.flat], file=file)
        print('D:', file=file)
        print(self.D, file=file)
        print([d for d in self.D.flat], file=file)
        print('', file=file)


    def solve(self, n=1, verbose=False):
        """Solve n optimization problems, and return argmin array."""
        for i in range(n):
            c = rand_matrix(3,1)
            x = cvx.Variable(name='x')
            y = cvx.Variable(name='y')
            z = cvx.Variable(name='z')
            # dummy variable to code semidefinite constraint
            T = cvx.SDPVar(5,name='T')
            spec = self.A * x + self.B * y + self.C * z + self.D
            obj = cvx.Minimize(c[0,0]*x + c[1,0]*y + c[2,0]*z)

            # check psd component
            if self.psd_spec:
                psd_status = cvx.get_status(
                    cvx.Problem(obj, [T == spec]).solve(verbose=verbose)
                )
                if psd_status == cvx.SOLVED:
                    self.mins.append([x.value, y.value, z.value])
                elif psd_status == cvx.INFEASIBLE:
                    self.psd_spec = False

            # check NSD component
            if self.nsd_spec:
                nsd_status = cvx.get_status(
                    cvx.Problem(obj, [T == -spec]).solve(verbose=verbose)
                )
                if nsd_status == cvx.SOLVED:
                    self.mins.append([x.value, y.value, z.value])
                    if psd_status == cvx.SOLVED:
                        self.fully_bounded_directions += 1
                elif nsd_status == cvx.UNBOUNDED \
                     and psd_status == cvx.UNBOUNDED:
                    self.fully_unbounded_directions += 1
                elif nsd_status == cvx.INFEASIBLE:
                    self.nsd_spec = False

        self.trials += n


    def get_nodes(self, handler=None):
        """Determine location of nodes, and classify them.

        handler() must output nodes as a list of points.

        """
        if handler is None:
            handler = self.get_nodes_from_singular
        for vector in handler():
            e = self.eigenvalues(vector)
            if (e[0] >= 0 and e[1] >= 0 and e[2] >= 0) \
               or (e[0] <= 0 and e[1] <= 0 and e[2] <= 0):
                self.spec_nodes.append([vector,e])
            else:
                self.sym_nodes.append([vector,e])


    def process(self, tolerance=1e-3):
        """Process minima to determine number of occurances.

        Points x and y are considered identical if
        norm(x-y)/norm(x) < tolerance, using the L2 norm.

        """
        if not self.spec_nodes and not self.sym_nodes:
            output = self.get_nodes()
            self.pmins = [[node[0], 0, node[1]]
                          for node in self.spec_nodes]
        if self.pmins:
            maxdelta = tolerance * max([linalg.norm(y[0]) for y in self.pmins])
            for y in self.pmins:
                yy = numpy.array(y[0])
                for x in self.mins:
                    delta = linalg.norm(numpy.array(x)-yy)
                    if delta <= maxdelta:
                        y[1] += 1
        # zero out mins once all elements are processed
        self.mins = []


    def gen_nodes(self, threshold=3, eival_tol=1e-4):
        """Fetch all nodes with percent of minima occuring at each.

        threshold: minimum number of points to be considered a node.
        If |x-y|/|x| < rel_threshold, discard whichever of x and y has
        fewer points.

        """
        if self.mins != [] or not self.sym_nodes:
            self.process()

        self.nodes = []
        if self.trials is not 0:
            for i in self.pmins:
                self.nodes.append([i[0], i[1] / self.trials, i[2]])
            self.nodes.sort(key=lambda x: x[1], reverse=True)
        else:
            for i in self.pmins:
                self.nodes.append([i[0], 0, i[2]])
         

    def print_results(self, file=None):
        if self.nodes == []:
            self.gen_nodes()
        print("spectrahedral nodes: {0}".format(len(self.pmins)), file=file)
        print("symmetroid nodes: {0}".format(
            len(self.sym_nodes) + len(self.pmins)
        ), file=file)
        print("", file=file)

        if self.trials is not 0:
            print("has psd component: {0}".format(self.psd_spec), file=file)
            print("has nsd component: {0}".format(self.nsd_spec), file=file)
            if self.psd_spec and self.nsd_spec:
                print("fraction of twice-solvable objectives: {0}".format(
                    self.fully_bounded_directions / self.trials
                ), file=file)
                print("fraction of twice-unbounded objectives: {0}".format(
                    self.fully_unbounded_directions / self.trials
                ), file=file)
            print("", file=file)

        for i in range(len(self.nodes)):
            print("node {0}:".format(i+1), file=file)
            print("location: {0}".format(self.nodes[i][0]), file=file)
            if self.trials is not 0:
                print("probability: {0}".format(self.nodes[i][1]), file=file)
            print("eigenvalues:", file=file)
            print(self.nodes[i][2], file=file)
            print('', file=file)
        for i in range(len(self.sym_nodes)):
            print("symmetroid node {0}:".format(i+1), file=file)
            print("location: {0}".format(self.sym_nodes[i][0]), file=file)
            print("eigenvalues:", file=file)
            print(self.sym_nodes[i][1], file=file)
            print("", file=file)
