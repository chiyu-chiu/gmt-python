"""
ctypes wrappers for core functions from the C API
"""
import os
import ctypes
from tempfile import NamedTemporaryFile
from contextlib import contextmanager

from ..exceptions import GMTCLibError, GMTCLibNoSessionError
from .utils import load_libgmt, kwargs_to_ctypes_array


class LibGMT():  # pylint: disable=too-many-instance-attributes
    """
    Load and access the GMT shared library (libgmt).

    Works as a context manager to create a GMT C API session and destroy it in
    the end. The context manager feature eliminates the need for the
    ``GMT_Create_Session`` and ``GMT_Destroy_Session`` functions. Thus, they
    are not exposed in the Python API. If you need the void pointer to the GMT
    session, use the ``current_session`` attribute.

    Functions of the shared library are exposed as methods of this class. Most
    methods MUST be used inside the context manager 'with' block.

    If creating GMT data structures to communicate data, put that code inside
    this context manager to reuse the same session.

    Parameters
    ----------
    libname : str
        The name of the GMT shared library **without the extension**. Can be a
        full path to the library or just the library name.

    Raises
    ------
    GMTCLibNotFoundError
        If there was any problem loading the library (couldn't find it or
        couldn't access the functions).
    GMTCLibNoSessionError
        If you try to call a method outside of a 'with' block.

    Examples
    --------

    >>> with LibGMT() as lib:
    ...     lib.call_module('figure', 'my-figure')

    """

    data_families = [
        'GMT_IS_DATASET',
        'GMT_IS_GRID',
        'GMT_IS_PALETTE',
        'GMT_IS_MATRIX',
        'GMT_IS_VECTOR',
    ]

    data_vias = [
        'GMT_VIA_MATRIX',
        'GMT_VIA_VECTOR',
    ]

    data_geometries = [
        'GMT_IS_NONE',
        'GMT_IS_POINT',
        'GMT_IS_LINE',
        'GMT_IS_POLYGON',
        'GMT_IS_PLP',
        'GMT_IS_SURFACE',
    ]

    data_modes = [
        'GMT_CONTAINER_ONLY',
        'GMT_OUTPUT',
    ]

    def __init__(self, libname='libgmt'):
        self._logfile = None
        self._session_id = None
        self._libgmt = None
        self._c_get_enum = None
        self._c_create_session = None
        self._c_destroy_session = None
        self._c_call_module = None
        self._c_create_data = None
        self._c_handle_messages = None
        self._bind_clib_functions(libname)

    @property
    def current_session(self):
        """
        The C void pointer for the current open GMT session.

        Raises
        ------
        GMTCLibNoSessionError
            If trying to access without a currently open GMT session (i.e.,
            outside of the context manager).

        """
        if self._session_id is None:
            raise GMTCLibNoSessionError(' '.join([
                "No currently open session.",
                "Call methods only inside a 'with' block."]))
        return self._session_id

    @current_session.setter
    def current_session(self, session):
        """
        Set the session void pointer.
        """
        self._session_id = session

    def _bind_clib_functions(self, libname):
        """
        Loads libgmt and binds the library functions to class attributes.

        Sets the argument and return types for the functions.
        """
        self._libgmt = load_libgmt(libname)

        self._c_create_session = self._libgmt.GMT_Create_Session
        self._c_create_session.argtypes = [ctypes.c_char_p, ctypes.c_uint,
                                           ctypes.c_uint, ctypes.c_void_p]
        self._c_create_session.restype = ctypes.c_void_p

        self._c_destroy_session = self._libgmt.GMT_Destroy_Session
        self._c_destroy_session.argtypes = [ctypes.c_void_p]
        self._c_destroy_session.restype = ctypes.c_int

        self._c_get_enum = self._libgmt.GMT_Get_Enum
        self._c_get_enum.argtypes = [ctypes.c_char_p]
        self._c_get_enum.restype = ctypes.c_int

        self._c_call_module = self._libgmt.GMT_Call_Module
        self._c_call_module.argtypes = [ctypes.c_void_p, ctypes.c_char_p,
                                        ctypes.c_int, ctypes.c_void_p]
        self._c_call_module.restype = ctypes.c_int

        self._c_create_data = self._libgmt.GMT_Create_Data
        self._c_create_data.argtypes = [
            ctypes.c_void_p,                  # API
            ctypes.c_uint,                    # family
            ctypes.c_uint,                    # geometry
            ctypes.c_uint,                    # mode
            ctypes.POINTER(ctypes.c_uint64),  # dim
            ctypes.POINTER(ctypes.c_double),  # range
            ctypes.POINTER(ctypes.c_double),  # inc
            ctypes.c_uint,                    # registration
            ctypes.c_int,                     # pad
            ctypes.c_void_p,                  # data
        ]
        self._c_create_data.restype = ctypes.c_void_p

        self._c_handle_messages = self._libgmt.GMT_Handle_Messages
        self._c_handle_messages.argtypes = [ctypes.c_void_p, ctypes.c_uint,
                                            ctypes.c_uint, ctypes.c_char_p]
        self._c_handle_messages.restype = ctypes.c_int

    def __enter__(self):
        """
        Start the GMT session and keep the session argument.
        """
        self.current_session = self.create_session('gmt-python-session')
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        """
        Destroy the session when exiting the context.
        """
        try:
            self.destroy_session(self.current_session)
        finally:
            self.current_session = None

    def create_session(self, session_name):
        """
        Create the ``GMTAPI_CTRL`` struct required by the GMT C API functions.

        It is a C void pointer containing the current session information and
        cannot be accessed directly.

        Remember to terminate the current session using
        :func:`gmt.clib.LibGMT.destroy_session` before creating a new one.

        Parameters
        ----------
        session_name : str
            A name for this session. Doesn't really affect the outcome.

        Returns
        -------
        api_pointer : C void pointer (returned by ctypes as an integer)
            Used by GMT C API functions.

        """
        # None is passed in place of the print function pointer. It becomes the
        # NULL pointer when passed to C, prompting the C API to use the default
        # print function.
        print_func = None
        padding = self.get_constant('GMT_PAD_DEFAULT')
        session_type = self.get_constant('GMT_SESSION_EXTERNAL')
        session = self._c_create_session(session_name.encode(), padding,
                                         session_type, print_func)

        if session is None:
            raise GMTCLibError("Failed to create a GMT API void pointer.")

        return session

    def destroy_session(self, session):
        """
        Terminate and free the memory of a registered ``GMTAPI_CTRL`` session.

        The session is created and consumed by the C API modules and needs to
        be freed before creating a new. Otherwise, some of the configuration
        files might be left behind and can influence subsequent API calls.

        Parameters
        ----------
        session : C void pointer (returned by ctypes as an integer)
            The active session object produced by
            :func:`gmt.clib.LibGMT.create_session`.
        libgmt : ctypes.CDLL
            The ``ctypes.CDLL`` instance for the libgmt shared library.

        """
        status = self._c_destroy_session(session)
        if status:
            raise GMTCLibError('Failed to destroy GMT API session')

    def get_constant(self, name):
        """
        Get the value of a constant (C enum) from gmt_resources.h

        Used to set configuration values for other API calls. Wraps
        ``GMT_Get_Enum``.

        Parameters
        ----------
        name : str
            The name of the constant (e.g., ``"GMT_SESSION_EXTERNAL"``)

        Returns
        -------
        constant : int
            Integer value of the constant. Do not rely on this value because it
            might change.

        Raises
        ------
        GMTCLibError
            If the constant doesn't exist.

        """
        value = self._c_get_enum(name.encode())

        if value is None or value == -99999:
            raise GMTCLibError(
                "Constant '{}' doesn't exits in libgmt.".format(name))

        return value

    @contextmanager
    def log_to_file(self, logfile=None):
        """
        Set the given session to log to a file.

        The file name is automatically generated by tempfile and stored in the
        ``_logfile`` attribute.
        """
        if logfile is None:
            tmp_file = NamedTemporaryFile(prefix='gmt-python-', suffix='.log',
                                          delete=False)
            logfile = tmp_file.name
            tmp_file.close()

        status = self._c_handle_messages(self.current_session,
                                         self.get_constant('GMT_LOG_ONCE'),
                                         self.get_constant('GMT_IS_FILE'),
                                         logfile.encode())
        if status != 0:
            msg = "Failed to set logging to file '{}' (error: {}).".format(
                logfile, status)
            raise GMTCLibError(msg)

        # The above is called when entering a 'with' statement
        yield logfile

        # Clean up when exiting the 'with' statement
        os.remove(logfile)

    def call_module(self, module, args):
        """
        Call a GMT module with the given arguments.

        Makes a call to ``GMT_Call_Module`` from the C API using mode
        ``GMT_MODULE_CMD`` (arguments passed as a single string).

        Most interactions with the C API are done through this function.

        Parameters
        ----------
        module : str
            Module name (``'pscoast'``, ``'psbasemap'``, etc).
        args : str
            String with the command line arguments that will be passed to the
            module (for example, ``'-R0/5/0/10 -JM'``).

        Raises
        ------
        GMTCLibError
            If the returned status code of the functions is non-zero.

        """
        mode = self.get_constant('GMT_MODULE_CMD')
        # If there is no open session, this will raise an exception. Can' let
        # it happen inside the 'with' otherwise the logfile won't be deleted.
        session = self.current_session
        with self.log_to_file() as logfile:
            status = self._c_call_module(session, module.encode(), mode,
                                         args.encode())
            # Get the error message inside the with block before the log file
            # is deleted
            with open(logfile) as flog:
                log = flog.read().strip()
        # Raise the exception outside the log 'with' to make sure the logfile
        # is cleaned.
        if status != 0:
            if log == '':
                msg = "Invalid GMT module name '{}'.".format(module)
            else:
                msg = '\n'.join([
                    "'{}' failed (error: {}).".format(module, status),
                    "---------- Error log ----------",
                    '',
                    log,
                    '',
                    "-------------------------------",
                ])
            raise GMTCLibError(msg)

    def create_data(self, family, geometry, mode, **kwargs):
        """
        Create an empty GMT data container.

        Parameters
        ----------
        family : str
            A valid GMT data family name (e.g., ``'GMT_IS_DATASET'``). See the
            ``data_families`` attribute for valid names.
        geometry : str
            A valid GMT data geometry name (e.g., ``'GMT_IS_POINT'``). See the
            ``data_geometries`` attribute for valid names.
        mode : str
            A valid GMT data mode (e.g., ``'GMT_OUTPUT'``). See the
            ``data_modes`` attribute for valid names.
        dim : list of 4 integers
            The dimensions of the dataset. See the documentation for the GMT C
            API function ``GMT_Create_Data`` (``src/gmt_api.c``) for the full
            range of options regarding 'dim'. If ``None``, will pass in the
            NULL pointer.
        ranges : list of 4 floats
            The dataset extent. Also a bit of a complicated argument. See the C
            function documentation. It's called ``range`` in the C function but
            it would conflict with the Python built-in ``range`` function.
        inc : list of 2 floats
            The increments between points of the dataset. See the C function
            documentation.
        registration : int
            The node registration (what the coordinates mean). Also a very
            complex argument. See the C function documentation. Defaults to
            ``GMT_GRID_NODE_REG``.
        pad : int
            The grid padding. Defaults to ``GMT_PAD_DEFAULT``.

        Returns
        -------
        data_ptr : int
            A ctypes pointer (an integer) to the allocated ``GMT_Dataset``
            object.

        Raises
        ------
        GMTCLibError
            In case of invalid inputs or data_ptr being NULL.

        """
        # Parse and check input arguments
        family_int = self._parse_data_family(family)
        if mode not in self.data_modes:
            raise GMTCLibError("Invalid data creation mode '{}'.".format(mode))
        if geometry not in self.data_geometries:
            raise GMTCLibError("Invalid data geometry '{}'.".format(geometry))

        # Convert dim, ranges, and inc to ctypes arrays if given
        dim = kwargs_to_ctypes_array('dim', kwargs, ctypes.c_uint64*4)
        ranges = kwargs_to_ctypes_array('ranges', kwargs, ctypes.c_double*4)
        inc = kwargs_to_ctypes_array('inc', kwargs, ctypes.c_double*2)

        # Use the GMT defaults if no value is given
        registration = kwargs.get('registration',
                                  self.get_constant('GMT_GRID_NODE_REG'))
        pad = kwargs.get('pad', self.get_constant('GMT_PAD_DEFAULT'))

        data_ptr = self._c_create_data(
            self.current_session,
            family_int,
            self.get_constant(geometry),
            self.get_constant(mode),
            dim,
            ranges,
            inc,
            registration,
            pad,
            None,  # NULL pointer for existing data
        )

        if data_ptr is None:
            raise GMTCLibError("Failed to create an empty GMT data pointer.")

        return data_ptr

    def _parse_data_family(self, family):
        """
        Parse the data family string into an integer  number.

        Valid family names are: GMT_IS_DATASET, GMT_IS_GRID, GMT_IS_PALETTE,
        GMT_IS_TEXTSET, GMT_IS_MATRIX, and GMT_IS_VECTOR.

        Optionally append a "via" argument to a family name (separated by
        ``|``): GMT_VIA_MATRIX or GMT_VIA_VECTOR.

        Parameters
        ----------
        family : str
            A GMT data family name.

        Returns
        -------
        family_value : int
            The GMT constant corresponding to the family.

        Raises
        ------
        GMTCLibError
            If the family name is invalid or there are more than 2 components
            to the name.

        """
        parts = family.split('|')
        if len(parts) > 2:
            raise GMTCLibError(
                "Too many sections in family (>2): '{}'".format(family))
        family_name = parts[0]
        if family_name not in self.data_families:
            raise GMTCLibError(
                "Invalid data family '{}'.".format(family_name))
        family_value = self.get_constant(family_name)
        if len(parts) == 2:
            via_name = parts[1]
            if via_name not in self.data_vias:
                raise GMTCLibError(
                    "Invalid data family (via) '{}'.".format(via_name))
            via_value = self.get_constant(via_name)
        else:
            via_value = 0
        return family_value + via_value
