from flask import current_app, request, signals
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
        self._instrument_app(app)

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
        if current_span:
            scope = self._span_manager.pop()
            scope.close()

    def end_all_spans(self):
        while self._span_manager.current is not None:
            self.end_active_span()

    def _instrument_app(self, app):
        signals.template_rendered.connect(self._template_rendered, app)
        signals.before_render_template.connect(self._before_template_rendered, app)
        signals.request_started.connect(self._request_started, app)
        signals.request_finished.connect(self._request_finished, app)
        signals.got_request_exception.connect(self._got_request_exception, app)
        signals.request_tearing_down.connect(self._request_tearing_down, app)
        signals.appcontext_tearing_down.connect(self._appcontext_tearing_down, app)
        signals.appcontext_pushed.connect(self._appcontext_pushed, app)
        signals.appcontext_popped.connect(self._appcontext_popped, app)
        signals.message_flashed.connect(self._message_flashed, app)
        app.teardown_appcontext(lambda *_, **__: self.end_all_spans())

    def _template_rendered(self, sender, template, context, **extras):
        self.end_active_span()

    def _before_template_rendered(self, sender, template, context, **extras):
        template_name = template.name or "<STRING>"
        operation = f"Jinja Rendering: {template_name}"
        span = self.start_active_span(operation)
        span.set_tag("Rendering Template", template_name)

    def _request_started(self, sender, **extras):
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

    def _request_finished(self, sender, response, **extras):
        span = current_span

        # we might not have started a span for this particular request
        if span:
            span.set_tag(tags.HTTP_STATUS_CODE, response.status_code)

    def _got_request_exception(self, sender, exception, **extras):
        span = current_span

        # we might not have started a span for this particular request
        if span:
            span.set_tag(tags.ERROR, True)
            exc_type = type(exception)

            module = getattr(exception, "__module__", "")

            name = exc_type.__name__

            if module:
                name = f"{module}.{exc_type.__name__}"

            span.log_kv({"Type": name, "Message": str(exception)})

    def _request_tearing_down(self, sender, **extras):
        pass

    def _appcontext_tearing_down(self, sender, **extras):
        pass

    def _appcontext_pushed(self, sender, **extras):
        pass

    def _appcontext_popped(self, sender, **extras):
        pass

    def _message_flashed(self, sender, message, category, **extras):
        pass


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

    @property
    def current(self):
        try:
            return _tracing_span_ctx_stack.top[1]
        except TypeError:
            return None


def __get_tracing():
    try:
        return current_app.extensions["tracing"]
    except (AttributeError, KeyError):
        raise RuntimeError("OpenTracing-Flask not configured")


tracing = LocalProxy(__get_tracing, name="opentracing-flask")
