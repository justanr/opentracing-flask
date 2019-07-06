from flask import current_app, request
from opentracing import Format, child_of, global_tracer, tags
from werkzeug.local import LocalProxy, LocalStack

_tracing_span_ctx_stack = LocalStack()


@LocalProxy
def current_span():
    rv = _tracing_span_ctx_stack.top

    if rv is None:
        return None

    return rv[1].span


class OpenTracingFlask:
    def __init__(
        self,
        app=None,
        tracer=None,
        global_tags=None,
        trace_static=False,
        request_filter=None,
    ):
        self._span_manager = _ActiveSpanManager()
        self._trace_static = trace_static

        if request_filter is None:
            request_filter = lambda request: True  # noqa

        self._request_filter = request_filter

        if global_tags is None:
            global_tags = {}

        self._global_tags = global_tags

        self._tracer_fn = global_tracer

        if tracer is not None:
            if callable(tracer):
                self._tracer_fn = tracer
            else:
                self._tracer_fn = lambda: tracer

        if app is not None:
            self.app = app
            self.init_app(app)

    def init_app(self, app):
        if not hasattr(app, "extensions"):
            app.extensions = {}

        app.extensions["tracing"] = self

        app.before_request(self._before_request)
        app.after_request(self._after_request)
        app.teardown_request(self._teardown_request)

    @property
    def _tracer(self):
        return self._tracer_fn()

    def start_active_span(self, operation, parent=None):
        scope = self._tracer.start_active_span(
            operation_name=operation, child_of=parent, tags=self._global_tags.copy()
        )
        self._span_manager.push(scope)
        return scope.span

    def end_active_span(self):
        scope = self._span_manager.pop()
        scope.close()

    def _before_request(self):
        endpoint = request.endpoint if request.endpoint else "[UNMATCHED]"

        if endpoint == "static" and not self._trace_static:
            return

        if not self._request_filter(request):
            return

        tracer = self._tracer
        parent = tracer.extract(Format.HTTP_HEADERS, carrier=request.headers)

        operation = f"Http In {request.method} {request.path}"
        span = self.start_active_span(operation, parent=parent)
        span.set_tag(tags.COMPONENT, "Flask")
        span.set_tag(tags.HTTP_METHOD, request.method)
        span.set_tag(tags.HTTP_URL, request.path)
        span.set_tag(tags.SPAN_KIND, tags.SPAN_KIND_RPC_SERVER)

        span.set_tag("endpoint", endpoint)

    def _after_request(self, response):
        span = current_span

        # we might not have started a span for this particular request
        if span:
            span.set_tag(tags.HTTP_STATUS_CODE, response.status_code)

        return response

    def _teardown_request(self, exception):
        span = current_span

        # we might not have started a span for this particular request
        if span:
            if exception:
                span.set_tag(tags.ERROR, True)
                span.log_kv({"Kind": type(exception), "Message": str(exception)})

            self.end_active_span()


class _ActiveSpanManager:
    def push(self, span):
        _tracing_span_ctx_stack.push((self, span))

    def pop(self):
        rv = _tracing_span_ctx_stack.pop()

        if rv is None or rv[0] is not self:
            raise RuntimeError(
                "popped wrong span context ({} instead of {})".format(rv, self)
            )

        return rv[1]


def __get_tracing():
    try:
        return current_app.extensions["tracing"]
    except (AttributeError, KeyError):
        raise RuntimeError("OpenTracing-Flask not configured")


tracing = LocalProxy(__get_tracing, name="opentracing-flask")
