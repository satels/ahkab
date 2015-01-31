# -*- coding: iso-8859-1 -*-
# symbolic.py
# Symbolic simulation module
# Copyright 2010-2013 Giuseppe Venturini

# This file is part of the ahkab simulator.
#
# Ahkab is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, version 2 of the License.
#
# Ahkab is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License v2
# along with ahkab.  If not, see <http://www.gnu.org/licenses/>.

"""
This module provides the functionality needed to perform a small-signal symbolic
simulation.

The principal method is :func:`symbolic_analysis`, which performs the symbolic
circuit solution.

.. note::

    This module is geared towards *setting up and running* the symbolic
    simulation.  Typically, it should be used in conjunction with
    :class:`ahkab.results.symbolic_solution`, the symbolic solution class, which
    holds several convenience methods to extensively manipulate, simplify,
    post-process and analyze the simulation results, complementing the
    functionality offered by the Sympy module itself.

Reference
\"\"\"\"\"\"\"\"\"

"""

from __future__ import (unicode_literals, absolute_import,
                        division, print_function)

import sympy
from sympy.matrices import zeros as smzeros

from . import circuit
from . import devices
from . import ekv
from . import mosq
from . import diode
from . import printing
from . import results
from . import options

specs = {'symbolic': {'tokens': ({
                                  'label': 'tf',
                                  'pos': None,
                                  'type': str,
                                  'needed': False,
                                  'dest': 'source',
                                  'default': None
                                 },
                                 {
                                  'label': 'ac',
                                  'pos': None,
                                  'type': bool,
                                  'needed': False,
                                  'dest': 'ac_enable',
                                  'default': True
                                 },
                                 {
                                  'label': 'r0s',
                                  'pos': None,
                                  'type': bool,
                                  'needed': False,
                                  'dest': 'r0s',
                                  'default': False
                                 }
                                )
                     }
        }

# the s variable
s = sympy.Symbol('s', complex=True)

enabled_assumptions = {'real':False, 'positive':False, 'complex':True}

def symbolic_analysis(circ, source=None, ac_enable=True, r0s=False, subs=None, outfile=None, verbose=3):
    """Attempt a symbolic, small-signal solution of the circuit.

    **Parameters:**

    circ : circuit instance
        the circuit instance to be simulated.

    source : string, optional
        the ``part_id`` of the source to be used as input for the transfer
           function. If ``None``, no transfer function is evaluated.

    ac_enable : bool, optional
        take frequency dependency into consideration (default: True).

    r0s : bool, optional
        take transistors' output impedance into consideration (default: False)

    subs: dict, optional
        a dictionary of ``sympy.Symbol`` to be substituted. It makes solving the circuit
        easier. Eg. ``subs={R1:R2}`` - replace R1 with R2. It can be generated with
        :func:`parse_substitutions()`

    outfile : string, optional
        output filename - ``'stdout'`` means print to stdout, the default.

    verbose: int, optional
        verbosity level 0 (silent) to 6 (painful).

    **Returns:** 

    sol : symbolic solution
        The solutions.

    tfs : symbolic solution
        The transfer functions, only if requested. Otherwise ``tfs`` is a
        ``None`` object.

    """
    if subs is None:
        subs = {}  # no subs by default

    if not ac_enable:
        printing.print_info_line(
            ("Starting symbolic DC analysis...", 1), verbose)
    else:
        printing.print_info_line(
            ("Starting symbolic AC analysis...", 1), verbose)

    printing.print_info_line(
        ("Building symbolic MNA, N and x...", 3), verbose, print_nl=False)
    mna, N, subs_g = generate_mna_and_N(
        circ, opts={'r0s': r0s}, ac=ac_enable, verbose=verbose)
    x = get_variables(circ)
    mna = mna[1:, 1:]
    N = N[1:, :]
    printing.print_info_line((" done.", 3), verbose)

    printing.print_info_line(
        ("Performing variable substitutions...", 5), verbose)
    mna, N = apply_substitutions(mna, N, subs)

    # for now, these crash sympy (0.7.5)
    #printing.print_info_line(("MNA matrix (reduced):", 5), verbose)
    #printing.print_info_line((sympy.sstr(mna), 5), verbose)
    #printing.print_info_line(("N matrix (reduced):", 5), verbose)
    #printing.print_info_line((sympy.sstr(N), 5), verbose)

    printing.print_info_line(("Building equations...", 3), verbose)
    eq = []
    for i in _to_real_list(mna * x + N):
        eq.append(sympy.Eq(i, 0))

    x = _to_real_list(x)

    if verbose > 3:
        printing.print_symbolic_equations(eq)
    printing.print_info_line(("To be solved for:", 4), verbose)
    printing.print_info_line((str(x), 4), verbose)
    printing.print_info_line(("Solving...", 1), verbose)

    sol = sympy.solve(
            eq, x, manual=options.symb_sympy_manual_solver, simplify=True)

    for ks in list(sol.keys()):
        sol.update({ks: sol[ks].subs(subs_g)})

    if sol == {}:
        printing.print_warning("No solutions. Check the netlist.")
    else:
        printing.print_info_line(("Success!", 2), verbose)
        printing.print_info_line(("Results:", 1), verbose)
        if options.cli:
            printing.print_symbolic_results(sol)

    if source is not None:
        src = _symbol_factory(source.upper())
        printing.print_info_line(("Calculating small-signal symbolic transfer functions (%s))..." %
                                 (str(src),), 2), verbose, print_nl=False)
        tfs = calculate_gains(sol, src)
        printing.print_info_line(("done.", 2), verbose)
        printing.print_info_line(
            ("Small-signal symbolic transfer functions:", 1), verbose)
        if options.cli:
            printing.print_symbolic_transfer_functions(tfs)
    else:
        tfs = None

    # convert to a results instance
    sol = results.symbolic_solution(sol, subs, circ, outfile)
    if tfs:
        if outfile and outfile != 'stdout':
            outfile += ".tfs"
        tfs = results.symbolic_solution(tfs, subs, circ, outfile, tf=True)
    return sol, tfs


def calculate_gains(sol, xin, optimize=True):
    """Calculate low-frequency gain and roots of a transfer function.

    **Parameters:**

    sol : dict
        the circuit solution
    xin : Sympy symbol
        the input variable
    optimize : boolean, optional
        If ``optimize`` is set to ``False``, no algebraic simplification
        will be attempted on the results. The default (``optimize=True``)
        results in ``sympy.together`` being called on each expression.

    **Returns:**

    gs : dict
        A dictionary with as keys the strings <key>/<xin> and as values
        dictionaries with keys ``'gain'``, ``'gain0'``, ``'poles'``,
        ``'zeros'``.

    """
    gains = {}
    for key, value in sol.items():
        tf = {}
        gain = sympy.together(value.diff(xin)) if optimize else value.diff(xin)
        (ps, zs) = get_roots(gain)
        tf.update({'gain': gain})
        tf.update({'gain0': gain.subs(s, 0)})
        tf.update({'poles': ps})
        tf.update({'zeros': zs})
        gains.update({"%s/%s" % (str(key), str(xin)): tf})
    return gains


# not used anymore. Superseeded by results.py
#def sol_to_dict(sol, x, optimize=True):
#    ret = {}
#    for index in range(x.shape[0]):
#        sol_current = sympy.together(sol[index]) if optimize else sol[index]
#        ret.update({str(x[index]): sol_current})
#    return ret


def apply_substitutions(mna, N, subs):
    """Apply the given a dictionary of substitutions.

    The actual sustitution is performed calling Sympy's
    ``subs()``.

    Example of substitution:

    ::

        R1 = 5*R2


    The above example can be carried out supplying the dictionary::

        subs = {R1:5*R2}


    **Parameters:**

    mna : Sympy matrix
        The MNA matrix.
    N : Sympy matrix
        The constant term N.
    subs : dict
        The dictionary of symbols substitutions.

    **Returns:**

    mna, N : ndarrays
        The same matrices with the substitutions applied.

    """
    mna = mna.subs(subs)
    N = N.subs(subs)
    return mna, N


def get_variables(circ):
    """Get a sympy matrix containing the circuit variables to be solved for.

    **Parameters:**

    circ : circuit instance
        The circuit

    **Returns:**
    
    vars : sympy matrix, shape (n, 1)
        The variables in a column vector.
    """
    # numero di soluzioni di tensione (al netto del ref)
    nv_1 = len(circ.nodes_dict) - 1

    # descrizioni dei componenti non definibili in tensione
    idescr = [elem.part_id.upper()
              for elem in circ if circuit.is_elem_voltage_defined(elem)]

    mna_size = nv_1 + len(idescr)
    x = smzeros(mna_size, 1)

    for i in range(mna_size):
        if i < nv_1:
            x[i, 0] = _symbol_factory("V" + str(circ.nodes_dict[i + 1]))
        else:
            x[i, 0] = _symbol_factory("I[" + idescr[i - nv_1] + "]")
    return x


def _to_real_list(M):
    """
    M.tolist() returns a list of lists, even when the symb matrix is really just a vector.
    we want a list of symbols! This fixes that.

    mylist[k] = mymat.tolist[k][0]

    M: a sympy matrix with only one column

    Returns: a list.
    """
    fakelist = M.tolist()
    reallist = []
    for elem in fakelist:
        reallist.append(elem[0])
    return reallist


def generate_mna_and_N(circ, opts, ac=False, verbose=3):
    """Generate a symbolic Modified Nodal Analysis matrix and N vector.

    **Parameters:**

    circ : circuit instance
        The circuit.
    opts : dict
        The options to be used for the generation of the matrices. As of now,
        the only supported option is ``'r0s'`` which can be set to either 
        ``True`` or ``False``, and selects whether the equivalent output
        resistance of the transistors should be taken into account or not.
    ac : bool, optional
        Flag to trigger the inclusion of frequency-dependent elements. Defaults
        to ``False`` currently (but may change).
    verbose : int, optional
        Verbosity flag, from ``0`` (silent) to ``6`` (very logorrhoic). Defaults
        to ``3``.

    **Returns:**

    mna, N : Sympy matrices
        The MNA matrix and the contant term of symbolic type.

    .. note::
        
        Setting ``opts['r0s'] = True``, ie considering all the transistors output
        resistances, can significantly slow down -- or even prevent by consuming
        all available memory -- the solution of complex circuits with several
        active elements.

        We recommend a combination of the following:
        
        * setting the above option in simple circuits only,
        * inserting explicitely the :math:`r_0` you wish to consider at circuit
          level, 
        * beefing up your machine with extra RAM and extra computing power,
        * being patient.
    """
    n_of_nodes = len(circ.nodes_dict)
    mna = smzeros(n_of_nodes)
    N = smzeros(n_of_nodes, 1)
    subs_g = {}

    for elem in circ:
        if isinstance(elem, devices.Resistor):
            # we use conductances instead of 1/R because there is a significant
            # overhead handling many 1/R terms in sympy.
            if elem.is_symbolic:
                R = _symbol_factory(
                    elem.part_id.upper(), real=True, positive=True)
                G = _symbol_factory('G' + elem.part_id[1:], real=True, positive=True)
                # but we keep track of which is which and substitute back after
                # solving.
                subs_g.update({G: 1 / R})
            else:
                R = elem.value
                G = 1.0 / R
            mna[elem.n1, elem.n1] = mna[elem.n1, elem.n1] + G
            mna[elem.n1, elem.n2] = mna[elem.n1, elem.n2] - G
            mna[elem.n2, elem.n1] = mna[elem.n2, elem.n1] - G
            mna[elem.n2, elem.n2] = mna[elem.n2, elem.n2] + G
        elif isinstance(elem, devices.Capacitor):
            if ac:
                if elem.is_symbolic:
                    capa = _symbol_factory(
                        elem.part_id.upper(), real=True, positive=True)
                else:
                    capa = elem.value
                mna[elem.n1, elem.n1] = mna[elem.n1, elem.n1] + s * capa
                mna[elem.n1, elem.n2] = mna[elem.n1, elem.n2] - s * capa
                mna[elem.n2, elem.n2] = mna[elem.n2, elem.n2] + s * capa
                mna[elem.n2, elem.n1] = mna[elem.n2, elem.n1] - s * capa
            else:
                pass
        elif isinstance(elem, devices.Inductor):
            pass
        elif isinstance(elem, devices.GISource):
            if elem.is_symbolic:
                alpha = _symbol_factory(elem.part_id.upper(), real=True)
            else:
                alpha = elem.value
            mna[elem.n1, elem.sn1] = mna[elem.n1, elem.sn1] + alpha
            mna[elem.n1, elem.sn2] = mna[elem.n1, elem.sn2] - alpha
            mna[elem.n2, elem.sn1] = mna[elem.n2, elem.sn1] - alpha
            mna[elem.n2, elem.sn2] = mna[elem.n2, elem.sn2] + alpha
        elif isinstance(elem, devices.ISource):
            if elem.is_symbolic:
                IDC = _symbol_factory(elem.part_id.upper())
            else:
                IDC = elem.dc_value
            N[elem.n1, 0] = N[elem.n1, 0] + IDC
            N[elem.n2, 0] = N[elem.n2, 0] - IDC
        elif isinstance(elem, mosq.mosq_device) or isinstance(elem, ekv.ekv_device):
            gm = _symbol_factory('gm_' + elem.part_id, real=True, positive=True)
            mna[elem.n1, elem.ng] = mna[elem.n1, elem.ng] + gm
            mna[elem.n1, elem.n2] = mna[elem.n1, elem.n2] - gm
            mna[elem.n2, elem.ng] = mna[elem.n2, elem.ng] - gm
            mna[elem.n2, elem.n2] = mna[elem.n2, elem.n2] + gm
            if opts['r0s']:
                r0 = _symbol_factory(
                    'r0_' + elem.part_id, real=True, positive=True)
                mna[elem.n1, elem.n1] = mna[elem.n1, elem.n1] + 1 / r0
                mna[elem.n1, elem.n2] = mna[elem.n1, elem.n2] - 1 / r0
                mna[elem.n2, elem.n1] = mna[elem.n2, elem.n1] - 1 / r0
                mna[elem.n2, elem.n2] = mna[elem.n2, elem.n2] + 1 / r0
        elif isinstance(elem, diode.diode):
            gd = _symbol_factory("g" + elem.part_id, positive=True)
            mna[elem.n1, elem.n1] = mna[elem.n1, elem.n1] + gd
            mna[elem.n1, elem.n2] = mna[elem.n1, elem.n2] - gd
            mna[elem.n2, elem.n1] = mna[elem.n2, elem.n1] - gd
            mna[elem.n2, elem.n2] = mna[elem.n2, elem.n2] + gd
        elif isinstance(elem, devices.FISource):
            # These are added after all VDEs have been accounted for
            pass
        elif isinstance(elem, devices.InductorCoupling):
            pass
            # this is taken care of within the inductors
        elif circuit.is_elem_voltage_defined(elem):
            pass
            # we'll add its lines afterwards
        elif verbose:
            printing.print_warning(
                "Skipped elem %s: not implemented." % (elem.part_id.upper(),))

    pre_vde = mna.shape[0]
    for elem in circ:
        if circuit.is_elem_voltage_defined(elem):
            index = mna.shape[0]  # get_matrix_size(mna)[0]
            mna = _expand_matrix(mna, add_a_row=True, add_a_col=True)
            N = _expand_matrix(N, add_a_row=True, add_a_col=False)
            # KCL
            mna[elem.n1, index] = +1
            mna[elem.n2, index] = -1
            # KVL
            mna[index, elem.n1] = +1
            mna[index, elem.n2] = -1
            if isinstance(elem, devices.VSource):
                if elem.is_symbolic:
                    VDC = _symbol_factory(elem.part_id.upper())
                else:
                    VDC = elem.dc_value
                N[index, 0] = -VDC
            elif isinstance(elem, devices.EVSource):
                if elem.is_symbolic:
                    alpha = _symbol_factory(elem.part_id.upper(), real=True)
                else:
                    alpha = elem.alpha
                mna[index, elem.sn1] = -alpha
                mna[index, elem.sn2] = +alpha
            elif isinstance(elem, devices.HVSource):
                if elem.is_symbolic:
                    alpha = _symbol_factory(elem.part_id.upper(), real=True)
                else:
                    alpha = elem.alpha
                source_index = circ.find_vde_index(elem.source_id)
                mna[index, n_of_nodes + source_index] = +alpha
            elif isinstance(elem, devices.Inductor):
                if ac:
                    if elem.is_symbolic:
                        L = _symbol_factory(
                            elem.part_id.upper(), real=True, positive=True)
                    else:
                        L = elem.L
                    mna[index, index] = -s * L
                else:
                    pass
                    # already so: commented out
                    # N[index,0] = 0
            else:
                raise circuit.CircuitError('Element %s is not supported. ' +
                                           'Please report this bug.' %
                                           elem.__class__)

    for elem in circ:
        if ac and isinstance(elem, devices.Inductor):
            # find its index to know which column corresponds to its
            # current
            this_index = circ.find_vde_index(elem.part_id, verbose=0)
            for cd in elem.coupling_devices:
                if cd.is_symbolic:
                    M = _symbol_factory(
                        cd.part_id, real=True, positive=True)
                else:
                    M = cd.K
                # get `part_id` of the other inductor (eg. "L32")
                other_id_wdescr = cd.get_other_inductor(elem.part_id)
                # find its index to know which column corresponds to
                # its current
                other_index = circ.find_vde_index(
                    other_id_wdescr, verbose=0)
                # add the term.
                mna[pre_vde + this_index,
                    pre_vde + other_index] += -s * M
        elif isinstance(elem, devices.FISource):
            source_current_index = circ.find_vde_index(elem.source_id, verbose=0)
            if elem.is_symbolic:
                F = _symbol_factory(elem.part_id, real=True)
            else:
                F = elem.alpha
            mna[elem.n1, pre_vde + source_current_index] += +F
            mna[elem.n2, pre_vde + source_current_index] += -F
        else:
            pass

    # all done
    return (mna, N, subs_g)


def _expand_matrix(mat, add_a_row=False, add_a_col=False):
    if add_a_row:
        row = smzeros(1, mat.shape[1])
        mat = mat.row_insert(mat.shape[0], row)
    if add_a_col:
        col = sympy.zeros(mat.shape[0], 1)
        mat = mat.col_insert(mat.shape[1], col)
    return mat


def get_roots(expr):
    """Given the transfer function ``expr``, returns ``poles, zeros``.
    """
    num, den = sympy.fraction(expr)
    return sympy.solve(den, s), sympy.solve(num, s)


def parse_substitutions(slist):
    """Generates a substitution dictionary from a substitution lists.

    The dictionary is typically then passed to :func:`symbolic_analysis`.

    **Parameters:**

    slist : a list of strings
        The elements of the list should be according to the syntax
        ``'<part_id1>=<part_id2>'``, eg ``'R2=R1'``, instructing the simulator
        to use the value of R1 (R1) instead of R2.

    **Returns:**

    subs : dict
        the dictionary of symbols to be passed to :func:`symbolic_analysis`.

    """
    subs = {}
    for l in slist:
        v1, v2 = l.split("=")
        letter_id1 = v1[0].upper() if v1[0].upper() != 'R' else 'G'
        letter_id2 = v2[0].upper() if v2[0].upper() != 'R' else 'G'
        if letter_id1[0] in ('R', 'G', 'L', 'C', 'M'):
            s1 = _symbol_factory(letter_id1 + v1[1:], real=True, positive=True)
        else:
            s1 = _symbol_factory(letter_id1 + v1[1:], real=True)
        if letter_id2[0] in ('R', 'G', 'L', 'C', 'M'):
            s2 = _symbol_factory(letter_id2 + v2[1:], real=True, positive=True)
        else:
            s2 = _symbol_factory(letter_id2 + v2[1:], real=True)
        subs.update({s1:s2})
    return subs

def _symbol_factory(name, **options):
    filtered_options = {}
    for i in options:
        if options[i] and enabled_assumptions[i]:
            filtered_options.update({i:options[i]})
        else:
            pass # discarded
    return sympy.Symbol(name.upper(), **filtered_options)
