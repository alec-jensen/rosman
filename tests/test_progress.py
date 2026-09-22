from io import StringIO

import pytest
from docker.errors import APIError
from rich.console import Console

from rosman.progress import DockerStreamError, NullReporter, RichReporter


def _failing_setup_stream(diagnostic: str):
    return iter(
        [
            {"stream": "Step 9/12 : RUN /tmp/rosman-setup.sh\n"},
            {"stream": f"{diagnostic}\n"},
            {"errorDetail": {"message": "The command returned a non-zero code: 42"}},
        ]
    )


@pytest.mark.parametrize("rich", [False, True])
def test_build_failure_retains_full_setup_script_output(rich):
    diagnostic = "setup.sh: missing dependency [" + "x" * 150 + "]"
    reporter = (
        RichReporter(Console(file=StringIO(), force_terminal=False))
        if rich
        else NullReporter()
    )

    with pytest.raises(DockerStreamError, match="non-zero code: 42") as error:
        reporter.build(_failing_setup_stream(diagnostic))

    assert error.value.build_output == (
        "Step 9/12 : RUN /tmp/rosman-setup.sh\n" + diagnostic + "\n"
    )


def test_successful_build_consumes_stream():
    NullReporter().build(iter([{"stream": "Step 1/1 : RUN true\n"}]))



def test_build_transport_failure_retains_prior_output():
    def stream():
        yield {"stream": "setup.sh: downloaded SDK\n"}
        raise APIError("Docker connection lost")

    with pytest.raises(DockerStreamError, match="Docker connection lost") as error:
        NullReporter().build(stream())

    assert error.value.build_output == "setup.sh: downloaded SDK\n"
